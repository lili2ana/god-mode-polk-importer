#!/usr/bin/env python3
"""FEMA MSC county NFHL archive -> private staging PostGIS -> local ST_Intersects.

Only official effective FEMA product NFHL_12071C (Lee County, FL) is accepted.
No data is inferred from STRAP identifiers or flood-map guesses. Runs only with
explicit staging DSN, and --write is required to change any database tables.

Examples:
  python run_lee_fema_static_postgis.py --download-dir /tmp/lee-fema
  python run_lee_fema_static_postgis.py --download-dir /tmp/lee-fema --write --intersect-limit 25
  python run_lee_fema_static_postgis.py --archive /secure/NFHL_12071C.zip \
      --official-source-confirmed --write --intersect-limit 25
  python run_lee_fema_static_postgis.py --intersect-only --write --intersect-limit 25

The offline --archive option requires explicit human confirmation that the ZIP
came from the listed official FEMA Map Service Center product page. A hash of
this specific archive is saved to the private dataset provenance table.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
import zipfile

import requests
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from shapely import wkb
from pyproj import Transformer

from run_lee_parcel_geometry_worker import open_staging_connection, staging_dsn_is_safe

FEMA_PRODUCT = 'NFHL_12071C'
FEMA_SOURCE_URL = 'https://msc.fema.gov/portal/downloadProduct?productID=NFHL_12071C'
MAX_ZIP_BYTES = 1_500_000_000
MAX_EXTRACTED_BYTES = 4_000_000_000
LOCAL_INTERSECTION_SQL = '''
    SELECT h.fld_zone, h.zone_subty, h.sfha_tf,
           extensions.ST_AsBinary(extensions.ST_Intersection(p.geom, h.geom)) AS clipped_wkb
    FROM god_mode_ops.lee_parcel_geometry_postgis p
    JOIN god_mode_ops.fema_nfhl_hazard_polygons h
      ON h.dataset_id=%s AND extensions.ST_Intersects(p.geom, h.geom)
    WHERE p.parcel_id=%s
'''
TO_UTM17 = Transformer.from_crs('EPSG:4326', 'EPSG:26917', always_xy=True)

class NFHLInputError(ValueError):
    pass


def download_url_allowed(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.hostname == 'msc.fema.gov'
                and p.path == '/portal/downloadProduct'
                and parse_qs(p.query) == {'productID':[FEMA_PRODUCT]}
                and not p.username and not p.password)
    except (TypeError, ValueError):
        return False


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def download_archive(dest: Path, source_url=FEMA_SOURCE_URL):
    if not download_url_allowed(source_url):
        raise NFHLInputError('Not the canonical FEMA MSC county NFHL product URL')
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    size=0
    # Fail closed on network errors, HTML login pages, oversized responses, or non-ZIP bytes.
    with requests.get(source_url, stream=True, timeout=(20,120), allow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, 'wb') as output:
            for chunk in resp.iter_content(1024*1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_ZIP_BYTES:
                    raise NFHLInputError('FEMA archive size exceeds configured maximum')
                h.update(chunk)
                output.write(chunk)
    if size<10000 or not zipfile.is_zipfile(dest):
        raise NFHLInputError('FEMA endpoint did not return a valid GIS ZIP archive')
    return h.hexdigest()


def extract_safe_zip(zip_path: Path, destination: Path):
    if not zipfile.is_zipfile(zip_path):
        raise NFHLInputError('Not a ZIP archive')
    destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members=archive.infolist()
        if not members:
            raise NFHLInputError('Empty NFHL archive')
        total=0
        for item in members:
            name=PurePosixPath(item.filename.replace('\\','/'))
            if name.is_absolute() or '..' in name.parts or not name.parts:
                raise NFHLInputError('Unsafe path in FEMA ZIP')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise NFHLInputError('Archive contains symbolic link')
            total+=item.file_size
            if total>MAX_EXTRACTED_BYTES:
                raise NFHLInputError('FEMA extraction exceeds safe maximum')
        names=[i.filename.lower() for i in members]
        if not any('.gdb/' in s or s.endswith('.shp') for s in names):
            raise NFHLInputError('Archive lacks any GIS geodatabase or shapefile')
        archive.extractall(destination)
    gdbs=sorted(p for p in destination.rglob('*.gdb') if p.is_dir())
    if gdbs:
        return gdbs[0]
    shps=[p for p in destination.rglob('*.shp') if 'fld_haz' in p.stem.lower()]
    if shps:
        return sorted(shps)[0]
    raise NFHLInputError('Cannot locate S_Fld_Haz_Ar GIS layer')


def fema_category(zone, subtype='', sfha=None):
    zone=(zone or '').strip().upper()
    subtype=(subtype or '').strip().upper()
    sfha=(sfha or '').strip().upper()
    high={'A','AE','AH','AO','AR','A99','V','VE','A1-30','V1-30'}
    if zone in high:
        category='High Risk (A/V)'
    elif zone in {'X','B','C'}:
        shaded=(zone=='B' or '0.2' in subtype or ('SHADED' in subtype and 'UNSHADED' not in subtype))
        category='Moderate (X-Shaded)' if shaded else 'Low (X-Unshaded)'
    elif zone=='D':
        category='Undetermined (D)'
    elif zone=='OPEN WATER':
        category='Open Water'
    else:
        raise NFHLInputError('Unknown FEMA flood zone')
    if (sfha=='T') != (category=='High Risk (A/V)') and sfha in {'T','F'}:
        raise NFHLInputError('FEMA SFHA_TF conflicts with zone classification')
    return category


def _checked(poly):
    if poly.is_empty or poly.geom_type not in {'Polygon','MultiPolygon'} or not poly.is_valid or poly.area <=0:
        raise NFHLInputError('Invalid polygon in FEMA or Lee source')
    return poly


def interpret_local_intersections(parcel, intersections, coverage_required=99.5):
    """Inputs are geometries already projected into a local equal-units metric CRS.
    Partial high-risk coverage can be recorded as REVIEW_REQUIRED; partial low-risk
    coverage produces no classification (absence of data isn't evidence of Zone X).
    """
    parcel=_checked(parcel)
    zone_parts=defaultdict(list)
    all_parts=[]
    for zone,subtype,sfha,poly in intersections:
        poly=_checked(poly)
        clipped=parcel.intersection(poly)
        if clipped.is_empty or clipped.area <=0:
            continue
        category=fema_category(zone,subtype,sfha)
        zone_parts[((zone or '').strip().upper(),category)].append(clipped)
        all_parts.append(clipped)
    if not zone_parts:
        return None
    area=parcel.area
    covered=min(100,max(0,unary_union(all_parts).area/area*100))
    zone_areas={k:unary_union(parts).area for k,parts in zone_parts.items()}
    high=[k for k in zone_parts if k[1]=='High Risk (A/V)']
    if covered < coverage_required and not high:
        return None
    selected=max(high or zone_areas.keys(),key=lambda k:zone_areas[k])
    sfha_parts=[piece for k,parts in zone_parts.items() if k[1]=='High Risk (A/V)' for piece in parts]
    sfha_area=unary_union(sfha_parts).area if sfha_parts else 0
    return {
        'fema_flood_zone':selected[0], 'fema_flood_category':selected[1],
        'sfha_overlap_pct':round(min(100,sfha_area/area*100),4),
        'fema_coverage_pct':round(covered,4),
        'mixed_zones':len(zone_parts)>1,
        'review_status':('REVIEW_REQUIRED' if high or len(zone_parts)>1 or
            selected[1]=='Undetermined (D)' or covered<coverage_required
            else 'EVIDENCE_CAPTURED'),
        'overlap_breakdown':[{'flood_zone':k[0],'category':k[1],
            'parcel_overlap_pct':round(min(100,a/area*100),4)}
            for k,a in sorted(zone_areas.items(),key=lambda it:it[1],reverse=True)],
        'source_feature_count':len(intersections),
    }


def parcel_bbox(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT MIN(extensions.ST_XMin(geom)),MIN(extensions.ST_YMin(geom)),
                       MAX(extensions.ST_XMax(geom)),MAX(extensions.ST_YMax(geom))
                       FROM god_mode_ops.lee_parcel_geometry_postgis''')
        bounds=cur.fetchone()
    conn.rollback()
    if not bounds or any(v is None for v in bounds):
        raise NFHLInputError('No verified county parcel polygons in staging')
    return (float(bounds[0])-.015,float(bounds[1])-.015,
            float(bounds[2])+.015,float(bounds[3])+.015)


def read_fema_layer(path, bbox_4326):
    import pyogrio
    import geopandas as gpd
    if path.suffix.lower()=='.shp':
        layer=path.stem
    else:
        layers=pyogrio.list_layers(str(path))
        hits=[name for name,geomtype in layers if name.lower()=='s_fld_haz_ar'
              and 'Polygon' in str(geomtype)]
        if len(hits)!=1:
            raise NFHLInputError('Expected exactly one polygon S_Fld_Haz_Ar layer')
        layer=hits[0]
    info=pyogrio.read_info(str(path),layer=layer)
    fields={str(x).upper():str(x) for x in info.get('fields',[])}
    if not {'FLD_ZONE','FLD_AR_ID','SFHA_TF'}.issubset(fields):
        raise NFHLInputError('FEMA hazard layer lacks official hazard fields')
    from pyproj import Transformer
    crs=info.get('crs')
    if not crs: raise NFHLInputError('FEMA layer has no coordinate reference system')
    bounds=Transformer.from_crs('EPSG:4326',crs,always_xy=True).transform_bounds(*bbox_4326,densify_pts=21)
    cols=[fields[x] for x in ('FLD_ZONE','FLD_AR_ID','SFHA_TF')]
    if 'ZONE_SUBTY' in fields:cols.append(fields['ZONE_SUBTY'])
    gdf=pyogrio.read_dataframe(str(path),layer=layer,bbox=bounds,columns=cols)
    if gdf.empty: raise NFHLInputError('No FEMA hazard features intersect county parcel bounding box')
    if gdf.crs is None:raise NFHLInputError('FEMA dataframe lost source CRS')
    return gdf.to_crs('EPSG:4326'),fields


def load_dataset(conn, source_url, product_id, archive_sha256, frame, columns):
    if not download_url_allowed(source_url) or product_id != FEMA_PRODUCT:
        raise NFHLInputError('Not the official FEMA Lee County MSC product')
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO god_mode_ops.fema_nfhl_static_datasets
          (product_id,source_url,archive_sha256,load_status)
          VALUES(%s,%s,%s,'LOADING')
          ON CONFLICT(product_id,archive_sha256) DO UPDATE
          SET source_url=EXCLUDED.source_url
          RETURNING dataset_id,load_status''',(product_id,source_url,archive_sha256))
        dataset_id,status=cur.fetchone()
        if status=='VALIDATED':
            conn.rollback();return dataset_id,0
        required={name:columns[name] for name in ('FLD_ZONE','FLD_AR_ID','SFHA_TF')}
        fld_subtype=columns.get('ZONE_SUBTY')
        rows=[];seen=set()
        for _,record in frame.iterrows():
            fid=str(record[required['FLD_AR_ID']]).strip()
            zone=str(record[required['FLD_ZONE']]).strip().upper()
            sfha=str(record[required['SFHA_TF']]).strip().upper()
            subtype=str(record[fld_subtype]).strip() if fld_subtype else ''
            if not fid or fid.lower() in {'nan','none'} or fid in seen:
                raise NFHLInputError('Missing/duplicate FEMA source FLD_AR_ID')
            seen.add(fid)
            poly=_checked(record.geometry)
            minx,miny,maxx,maxy=poly.bounds
            if not(-83<minx<maxx<-80 and 25<miny<maxy<28):
                raise NFHLInputError('FEMA polygon outside expected southwest Florida area')
            fema_category(zone,subtype,sfha)
            if len(rows)>100000:
                raise NFHLInputError('Too many candidate flood hazard features in single staging load')
            rows.append((dataset_id,fid,zone,subtype,sfha,poly.wkt))
        if not rows:raise NFHLInputError('No validated FEMA hazard polygons after spatial cropping')
        for pos in range(0,len(rows),250):
            execute_values(cur,'''INSERT INTO god_mode_ops.fema_nfhl_hazard_polygons
                (dataset_id,source_fld_ar_id,fld_zone,zone_subty,sfha_tf,geom)
                VALUES %s''',rows[pos:pos+250],template="(%s,%s,%s,%s,%s,extensions.ST_Multi(extensions.ST_GeomFromText(%s,4326)))")
        cur.execute('''UPDATE god_mode_ops.fema_nfhl_static_datasets
            SET load_status='VALIDATED',feature_count=%s WHERE dataset_id=%s''',(len(rows),dataset_id))
    conn.commit()
    return dataset_id,len(rows)


def run_local_intersection(conn, dataset_id, limit=25):
    from psycopg2.extras import Json,execute_values
    with conn.cursor() as cur:
        cur.execute('''SELECT parcel_id,extensions.ST_AsBinary(geom)
          FROM god_mode_ops.lee_parcel_geometry_postgis p
          WHERE NOT EXISTS (SELECT 1 FROM god_mode_ops.lee_parcel_fema_intersections e
                            WHERE e.parcel_id=p.parcel_id)
          ORDER BY parcel_id LIMIT %s''',(limit,))
        candidates=cur.fetchall()
        cur.execute("SELECT source_url,load_status FROM god_mode_ops.fema_nfhl_static_datasets WHERE dataset_id=%s",(dataset_id,))
        meta=cur.fetchone()
        if not meta or meta[1]!='VALIDATED':
            raise NFHLInputError('FEMA archive not validated, no intersections permitted')
        source_url=meta[0]
        rows=[];incomplete=0
        for parcel_id,source_wkb in candidates:
            parcel=transform(TO_UTM17.transform,wkb.loads(bytes(source_wkb)))
            cur.execute(LOCAL_INTERSECTION_SQL,(dataset_id,parcel_id))
            features=[]
            for zone,subtype,sfha,clipped in cur.fetchall():
                if clipped is not None:
                    geom=transform(TO_UTM17.transform,wkb.loads(bytes(clipped)))
                    if not geom.is_empty and geom.area>0:
                        features.append((zone,subtype,sfha,geom))
            evidence=interpret_local_intersections(parcel,features)
            if evidence is None:
                incomplete+=1
                continue
            rows.append((parcel_id,evidence['fema_flood_zone'],evidence['fema_flood_category'],
                evidence['sfha_overlap_pct'],evidence['mixed_zones'],evidence['review_status'],
                Json(evidence['overlap_breakdown']),evidence['source_feature_count'],
                source_url,dataset_id,evidence['fema_coverage_pct']))
        if rows:
            execute_values(cur,'''INSERT INTO god_mode_ops.lee_parcel_fema_intersections
              (parcel_id,fema_flood_zone,fema_flood_category,sfha_overlap_pct,
               mixed_zones,review_status,overlap_breakdown,source_feature_count,
               source_url,local_dataset_id,fema_coverage_pct)
              VALUES %s ON CONFLICT(parcel_id) DO NOTHING''',rows)
            cur.execute('''UPDATE god_mode_ops.lee_parcel_api_jobs
              SET job_status='PARTIAL_LOCAL_FEMA_EVIDENCE_VERIFIED',
                  parcels_processed=(SELECT COUNT(*) FROM god_mode_ops.lee_parcel_fema_intersections),
                  endpoint_verified=true,block_reason=NULL,updated_at=now()
              WHERE source_name='FEMA_NFHL' ''')
    conn.commit()
    return len(candidates),len(rows),incomplete


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,help='Official FEMA ZIP downloaded by operator (requires attestation)')
    p.add_argument('--official-source-confirmed',action='store_true',help='Attest offline archive came directly from FEMA MSC')
    p.add_argument('--download-dir',type=Path,default=Path('/tmp/lee-nfhl'))
    p.add_argument('--write',action='store_true',help='Allow writing to staging only')
    p.add_argument('--intersect-only',action='store_true',help='Use an already VALIDATED local FEMA dataset')
    p.add_argument('--intersect-limit',type=int,default=25)
    args=p.parse_args(argv)
    if not 1<=args.intersect_limit<=500:
        p.error('--intersect-limit must be 1..500')
    if args.archive and not args.official_source_confirmed:
        p.error('Offline ZIP requires --official-source-confirmed and actual FEMA MSC source')
    dsn=os.environ.get('STAGING_SUPABASE_DB_URL','')
    if not staging_dsn_is_safe(dsn):
        raise NFHLInputError('Staging-only STAGING_SUPABASE_DB_URL required; no production DB access')
    conn=open_staging_connection(dsn)
    try:
        if args.intersect_only:
            with conn.cursor() as cur:
                cur.execute("""SELECT dataset_id FROM god_mode_ops.fema_nfhl_static_datasets
                  WHERE product_id=%s AND load_status='VALIDATED'
                  ORDER BY retrieved_at DESC LIMIT 1""",(FEMA_PRODUCT,))
                row=cur.fetchone()
            conn.rollback()
            if not row:raise NFHLInputError('No validated local FEMA archive to intersect')
            dataset_id=row[0]
        else:
            args.download_dir.mkdir(parents=True,exist_ok=True)
            archive=args.archive or args.download_dir/'NFHL_12071C.zip'
            if args.archive:
                if not archive.is_file() or not zipfile.is_zipfile(archive):
                    raise NFHLInputError('Offline FEMA archive missing or not a ZIP')
                digest=sha256_file(archive)
            else:
                digest=download_archive(archive)
            with tempfile.TemporaryDirectory(prefix='verified_nfhl_') as td:
                source=extract_safe_zip(archive,Path(td))
                frame,cols=read_fema_layer(source,parcel_bbox(conn))
                print(f'SOURCE CHECK: validated FEMA features in AOI={len(frame)} SHA256={digest}')
                if not args.write:
                    print('DRY RUN ONLY: No database writes or parcel classification.')
                    return 0
                dataset_id,loaded=load_dataset(conn,FEMA_SOURCE_URL,FEMA_PRODUCT,digest,frame,cols)
                print('Verified official static NFHL hazard polygons saved:',loaded)
        if not args.write:
            print('DRY RUN ONLY: No local intersections persisted.')
            return 0
        attempted,saved,unverified=run_local_intersection(conn,dataset_id,args.intersect_limit)
        print(f'Local PostGIS ST_Intersects attempted={attempted}, verified={saved}, incomplete_coverage={unverified}')
        print('No ownership, wetlands, recorded access, delinquency, qualification or outreach changes.')
        return 0 if unverified==0 else 1
    finally:
        conn.close()

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(f'FEMA static-local pipeline BLOCKED: {type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(2)