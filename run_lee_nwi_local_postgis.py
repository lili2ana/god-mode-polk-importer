#!/usr/bin/env python3
"""Stage official Florida NWI GeoPackage polygons and compute mapped coverage in PostGIS.

Source: https://www.fws.gov/program/national-wetlands-inventory/download-state-wetlands-data
Only the linked Florida GeoPackage is accepted. Database writes require --write and a
staging-project-only connection. Does not qualify leads or infer legal wetlands status.

  python run_lee_nwi_local_postgis.py --download-dir /tmp/fl-nwi --write --import-only
  python run_lee_nwi_local_postgis.py --write --intersect-only --limit 25

The GeoPackage can be large. First invocation downloads the source and stores the
archive SHA-256, then loads only features around current verified Lee parcels.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
from urllib.parse import urlsplit, unquote
import zipfile

import requests


NWI_SOURCE_URL = ('https://documentst.ecosphere.fws.gov/wetlands/data/'
                  'State-Downloads/FL_geopackage_wetlands.zip')
MAX_ARCHIVE_BYTES = 2_800_000_000
MAX_UNPACKED_BYTES = 8_000_000_000
MAX_MEMBERS = 150
WETLAND_TYPES = frozenset({
    'Freshwater Emergent Wetland', 'Freshwater Forested/Shrub Wetland',
    'Freshwater Pond', 'Estuarine and Marine Wetland',
})
WATERS_TYPES = frozenset({'Lake', 'Riverine', 'Estuarine and Marine Deepwater'})

class NWIError(ValueError):
    pass


def source_url_allowed(url):
    try:
        p = urlsplit(url)
        return (p.scheme == 'https' and p.netloc == 'documentst.ecosphere.fws.gov'
                and p.path == '/wetlands/data/State-Downloads/FL_geopackage_wetlands.zip'
                and not p.query and not p.fragment and not p.username)
    except (TypeError, ValueError):
        return False


def classify_type(value):
    v = str(value or '').strip()
    if v in WETLAND_TYPES:
        return 'WETLAND'
    if v in WATERS_TYPES:
        return 'AQUATIC_OTHER'
    raise NWIError('Unknown NWI wetland/deepwater category; block classification')


def staging_dsn_is_safe(dsn):
    try:
        parts = urlsplit(dsn)
        if parts.scheme not in ('postgresql','postgres') or not parts.hostname:
            return False
        host=parts.hostname.lower()
        user=unquote(parts.username or '')
        return (host == 'db.ltwqgkcblvtscgldxvjw.supabase.co' or
                (host.endswith('.pooler.supabase.com') and
                 user == 'postgres.ltwqgkcblvtscgldxvjw'))
    except (TypeError,ValueError):
        return False


def open_staging_connection(dsn):
    if not staging_dsn_is_safe(dsn):
        raise NWIError('Staging database is required')
    import psycopg2
    return psycopg2.connect(dsn,connect_timeout=12,
           sslmode='verify-full' if os.getenv('PGSSLROOTCERT') else 'require')


def read_database_dsn(env=None):
    env = os.environ if env is None else env
    dsn = env.get('STAGING_SUPABASE_DB_URL', '')
    if not staging_dsn_is_safe(dsn):
        raise NWIError('Dedicated Supabase staging DSN required')
    return dsn


def validate_archive(path):
    if not zipfile.is_zipfile(path):
        raise NWIError('Not a ZIP archive')
    with zipfile.ZipFile(path) as z:
        entries = z.infolist()
        if not 0 < len(entries) <= MAX_MEMBERS:
            raise NWIError('Unexpected archive member count')
        total, geopackages = 0, []
        for e in entries:
            name = PurePosixPath(e.filename.replace('\\','/'))
            if name.is_absolute() or '..' in name.parts or not name.parts:
                raise NWIError('Unsafe archive path')
            if (e.external_attr >> 16) & 0o170000 == 0o120000:
                raise NWIError('Archive symlinks are prohibited')
            total += e.file_size
            if total > MAX_UNPACKED_BYTES:
                raise NWIError('Archive is too large')
            if e.filename.lower().endswith('.gpkg'):
                geopackages.append(e)
        if len(geopackages) != 1:
            raise NWIError('Exactly one Florida NWI GeoPackage required')
        return geopackages[0].filename


def download_archive(target, source_url=NWI_SOURCE_URL):
    if not source_url_allowed(source_url):
        raise NWIError('Nonofficial NWI source URL rejected')
    target = Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256()
    size=0
    with requests.get(source_url, stream=True, timeout=(20,180), allow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect or response.status_code != 200:
            raise NWIError('Unexpected NWI download redirect or response')
        with target.open('wb') as dest:
            for chunk in response.iter_content(1024*1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise NWIError('NWI download exceeds size cap')
                digest.update(chunk)
                dest.write(chunk)
    if size < 10000:
        raise NWIError('Unexpectedly small NWI archive')
    validate_archive(target)
    return digest.hexdigest()


def sha256_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def unpack_geopackage(archive_path, directory):
    name = validate_archive(archive_path)
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    # Extract only the single validated GeoPackage, not other archive contents.
    out = directory/Path(name).name
    with zipfile.ZipFile(archive_path) as archive, archive.open(name) as source, out.open('wb') as target:
        import shutil
        shutil.copyfileobj(source, target, 1024*1024)
    return out


def validate_results(wetland_pct, mapped_pct, wetland_count, total_count):
    vals = (float(wetland_pct),float(mapped_pct))
    if not all(0 <= v <= 100.01 for v in vals) or vals[0]>vals[1]+0.01:
        raise NWIError('Invalid NWI union coverage percentages')
    if not (0 <= wetland_count <= total_count):
        raise NWIError('Invalid NWI source feature counts')
    return dict(
        nwi_wetland_pct=round(min(100,vals[0]),4),
        nwi_mapped_coverage_pct=round(min(100,vals[1]),4),
        matched_wetland_features=wetland_count,
        matched_total_features=total_count,
        review_status='REVIEW_REQUIRED',  # NWI isn't a regulatory or buildability determination
    )


SELECT_INTERSECTIONS_SQL = '''
WITH parcel AS (
  SELECT parcel_id, geom, extensions.ST_Area(extensions.ST_Transform(geom,26917)) AS parcel_area
  FROM god_mode_ops.lee_parcel_geometry_postgis WHERE parcel_id=%s
), overlapped AS (
  SELECT n.wetland_type,n.feature_class,
    extensions.ST_Intersection(p.geom,n.geom) AS clipped
  FROM parcel p JOIN god_mode_ops.nwi_wetlands_polygons n
    ON n.dataset_id=%s AND extensions.ST_Intersects(p.geom,n.geom)
), areas AS (
  SELECT COUNT(*) FILTER (WHERE feature_class='WETLAND' AND NOT extensions.ST_IsEmpty(clipped)) AS wetland_count,
         COUNT(*) FILTER (WHERE NOT extensions.ST_IsEmpty(clipped)) AS total_count,
         extensions.ST_UnaryUnion(extensions.ST_Collect(clipped)
           FILTER (WHERE NOT extensions.ST_IsEmpty(clipped))) AS all_geom,
         extensions.ST_UnaryUnion(extensions.ST_Collect(clipped)
           FILTER (WHERE feature_class='WETLAND' AND NOT extensions.ST_IsEmpty(clipped))) AS wetland_geom
  FROM overlapped
)
SELECT
 COALESCE(100.0*extensions.ST_Area(extensions.ST_Transform(a.wetland_geom,26917))
          /NULLIF(p.parcel_area,0),0)::double precision AS wetland_pct,
 COALESCE(100.0*extensions.ST_Area(extensions.ST_Transform(a.all_geom,26917))
          /NULLIF(p.parcel_area,0),0)::double precision AS mapped_pct,
 a.wetland_count,a.total_count
FROM parcel p CROSS JOIN areas a
'''


def read_lee_bbox(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT MIN(extensions.ST_XMin(geom)),MIN(extensions.ST_YMin(geom)),
                    MAX(extensions.ST_XMax(geom)),MAX(extensions.ST_YMax(geom))
                    FROM god_mode_ops.lee_parcel_geometry_postgis''')
        b=cur.fetchone()
    conn.rollback()
    if not b or any(x is None for x in b):
        raise NWIError('No verified Lee GIS polygons to screen')
    return (float(b[0])-.015,float(b[1])-.015,float(b[2])+.015,float(b[3])+.015)


def select_wetlands_layer(path):
    import pyogrio
    layers=pyogrio.list_layers(str(path))
    matches=[name for name,shape in layers if 'wetlands' in name.lower()
             and 'metadata' not in name.lower() and 'historic' not in name.lower()
             and 'polygon' in str(shape).lower()]
    if len(matches)!=1:
        raise NWIError('Ambiguous NWI wetlands polygon layer')
    info=pyogrio.read_info(str(path),layer=matches[0])
    fields={name.upper():name for name in info.get('fields',[])}
    typ=fields.get('WETLAND_TYPE') or fields.get('WETLAND_TY')
    attr=fields.get('ATTRIBUTE')
    if not typ or not attr or not info.get('crs'):
        raise NWIError('NWI wetlands layer missing WETLAND_TYPE/ATTRIBUTE/CRS')
    return matches[0],info['crs'],typ,attr


def read_bbox_features(path, layer, crs, typ, attr, bbox, size=1500):
    import pyogrio
    from pyproj import Transformer
    from shapely import wkb
    import geopandas as gpd
    query_bbox=Transformer.from_crs('EPSG:4326',crs,always_xy=True).transform_bounds(*bbox,densify_pts=21)
    offset=0
    while True:
        batch=pyogrio.read_dataframe(str(path),layer=layer,bbox=query_bbox,
                                      columns=[typ,attr],skip_features=offset,max_features=size)
        if batch.empty:
            return
        if batch.crs is None:
            raise NWIError('NWI GIS batch missing CRS')
        batch=batch.to_crs('EPSG:4326')
        for _,feature in batch.iterrows():
            geom=feature.geometry
            if geom is None or geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):
                raise NWIError('Invalid wetland geometry')
            if not (-83 < geom.bounds[0] < geom.bounds[2] < -80 and 25 < geom.bounds[1] < geom.bounds[3] < 28):
                raise NWIError('NWI result outside expected Lee region')
            code=str(feature[attr] or '').strip()
            typvalue=str(feature[typ] or '').strip()
            cls=classify_type(typvalue)
            digest=hashlib.sha256(geom.wkb+code.encode()+typvalue.encode()).hexdigest()
            yield digest,code,typvalue,cls,geom.wkt
        offset+=len(batch)
        if len(batch)<size:
            return


def import_dataset(conn, archive_path, unpack_dir, digest):
    from psycopg2.extras import execute_values
    path=unpack_geopackage(archive_path,unpack_dir)
    layer,crs,typ,attr=select_wetlands_layer(path)
    bounds=read_lee_bbox(conn)
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO god_mode_ops.nwi_static_datasets
          (source_url,archive_sha256,load_status,spatial_scope,feature_count)
          VALUES(%s,%s,'LOADING','LEE_QUEUE_BBOX',0)
          ON CONFLICT(source_url,archive_sha256) DO UPDATE
          SET source_url=EXCLUDED.source_url
          RETURNING dataset_id,load_status''',(NWI_SOURCE_URL,digest))
        dataset_id,status=cur.fetchone()
        if status=='VALIDATED':
            conn.rollback()
            return dataset_id,0
        cur.execute('DELETE FROM god_mode_ops.nwi_wetlands_polygons WHERE dataset_id=%s',(dataset_id,))
    conn.commit()
    count,batch_rows=0,[]
    for h,code,t,cls,wkt in read_bbox_features(path,layer,crs,typ,attr,bounds):
        batch_rows.append((dataset_id,h,code,t,cls,wkt))
        if len(batch_rows)>=400:
            with conn.cursor() as cur:
                execute_values(cur,'''INSERT INTO god_mode_ops.nwi_wetlands_polygons
                (dataset_id,feature_hash,attribute_code,wetland_type,feature_class,geom)
                VALUES %s ON CONFLICT(dataset_id,feature_hash) DO NOTHING''',batch_rows,
                template="(%s,%s,%s,%s,%s,extensions.ST_Multi(extensions.ST_GeomFromText(%s,4326)))")
            conn.commit();count+=len(batch_rows);batch_rows.clear()
    if batch_rows:
        with conn.cursor() as cur:
            execute_values(cur,'''INSERT INTO god_mode_ops.nwi_wetlands_polygons
            (dataset_id,feature_hash,attribute_code,wetland_type,feature_class,geom)
            VALUES %s ON CONFLICT(dataset_id,feature_hash) DO NOTHING''',batch_rows,
            template="(%s,%s,%s,%s,%s,extensions.ST_Multi(extensions.ST_GeomFromText(%s,4326)))")
        conn.commit();count+=len(batch_rows)
    if not count:
        raise NWIError('No NWI polygons in Lee bounding box; cannot validate source')
    with conn.cursor() as cur:
        cur.execute('''UPDATE god_mode_ops.nwi_static_datasets SET
          load_status='VALIDATED',feature_count=(SELECT count(*) FROM god_mode_ops.nwi_wetlands_polygons WHERE dataset_id=%s),
          validated_at=now() WHERE dataset_id=%s''',(dataset_id,dataset_id))
    conn.commit()
    return dataset_id,count


def validated_dataset(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT dataset_id,source_url FROM god_mode_ops.nwi_static_datasets
          WHERE load_status='VALIDATED' AND feature_count>0 ORDER BY validated_at DESC LIMIT 1""")
        result=cur.fetchone()
    conn.rollback()
    if not result or not source_url_allowed(result[1]):
        raise NWIError('No validated official Florida NWI static snapshot')
    return result


def intersect_parcels(conn,dataset_id,source_url,limit,write=False):
    processed=0;cursor=''
    while processed<limit:
        with conn.cursor() as cur:
            cur.execute('''SELECT p.parcel_id FROM god_mode_ops.lee_parcel_geometry_postgis p
              JOIN god_mode_ops.lee_parcel_qualification_screening s USING(parcel_id)
              LEFT JOIN god_mode_ops.lee_parcel_nwi_intersections i
                ON i.parcel_id=p.parcel_id AND i.dataset_id=%s
              WHERE s.county='Lee County' AND s.qualification_status='PENDING_SCREENING'
              AND i.parcel_id IS NULL AND p.parcel_id>%s
              ORDER BY p.parcel_id LIMIT %s''',(dataset_id,cursor,min(100,limit-processed)))
            ids=[r[0] for r in cur.fetchall()]
        conn.rollback()
        if not ids:break
        for pid in ids:
            cursor=pid
            with conn.cursor() as cur:
                cur.execute(SELECT_INTERSECTIONS_SQL,(pid,dataset_id))
                result=cur.fetchone()
            conn.rollback()
            if not result:
                raise NWIError('Missing parcel geometry during local intersection')
            record=validate_results(*result)
            if write:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO god_mode_ops.lee_parcel_nwi_intersections
                      (parcel_id,dataset_id,nwi_wetland_pct,nwi_mapped_coverage_pct,
                       matched_wetland_features,matched_total_features,review_status,
                       source_url,computed_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,now())
                      ON CONFLICT(parcel_id) DO UPDATE SET
                       dataset_id=EXCLUDED.dataset_id,nwi_wetland_pct=EXCLUDED.nwi_wetland_pct,
                       nwi_mapped_coverage_pct=EXCLUDED.nwi_mapped_coverage_pct,
                       matched_wetland_features=EXCLUDED.matched_wetland_features,
                       matched_total_features=EXCLUDED.matched_total_features,
                       review_status='REVIEW_REQUIRED',source_url=EXCLUDED.source_url,computed_at=now()''',
                       (pid,dataset_id,record['nwi_wetland_pct'],record['nwi_mapped_coverage_pct'],
                        record['matched_wetland_features'],record['matched_total_features'],
                        record['review_status'],source_url))
                conn.commit()
            processed+=1
    if write and processed:
        with conn.cursor() as cur:
            cur.execute('''UPDATE god_mode_ops.lee_parcel_api_jobs SET
              job_status='PARTIAL_NWI_POSTGIS_EVIDENCE_VERIFIED',
              parcels_processed=(SELECT count(*) FROM god_mode_ops.lee_parcel_nwi_intersections
                 WHERE dataset_id=%s), endpoint_verified=true,endpoint_checked_at=now(),
              verification_source_url=%s,block_reason=NULL,updated_at=now()
              WHERE source_name='USFWS_NWI' ''',(dataset_id,source_url))
        conn.commit()
    return processed


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--download-dir', type=Path)
    parser.add_argument('--import-only', action='store_true')
    parser.add_argument('--intersect-only', action='store_true')
    parser.add_argument('--limit', type=int,default=25)
    parser.add_argument('--write',action='store_true')
    opts=parser.parse_args(argv)
    if opts.import_only and opts.intersect_only:
        parser.error('Choose either import-only or intersect-only')
    if opts.limit<1 or opts.limit>2710:
        parser.error('--limit must be 1..2710')
    dsn=read_database_dsn()
    conn=open_staging_connection(dsn)
    try:
        if not opts.intersect_only:
            if not opts.write:
                print('DRY RUN: source URL verified; archive not downloaded without --write')
                return 0
            with tempfile.TemporaryDirectory() as tmp:
                base=opts.download_dir or Path(tmp)
                base.mkdir(parents=True,exist_ok=True)
                archive=opts.archive or (base/'FL_geopackage_wetlands.zip')
                digest=sha256_file(archive) if opts.archive else download_archive(archive)
                if opts.archive:
                    validate_archive(archive)
                dataset_id,count=import_dataset(conn,archive,base/'unpacked',digest)
                print('VALIDATED NWI dataset',dataset_id,'features loaded around Lee queue:',count)
        if not opts.import_only:
            dataset_id,source_url=validated_dataset(conn)
            count=intersect_parcels(conn,dataset_id,source_url,opts.limit,write=opts.write)
            print('NWI local parcel intersections',count,'write',opts.write)
            print('NWI wetland mapping is advisory, not a regulatory wetland or title determination.')
        return 0
    finally:
        conn.close()

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print('NWI fail-closed:',type(exc).__name__,file=sys.stderr)
        sys.exit(1)