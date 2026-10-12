-- Verified official parcel identities; do not substitute center-point proximity.
create or replace function god_mode_ops.county_gis_config(p_county text)
returns jsonb language sql immutable set search_path = '' as $$
select jsonb_build_object('county',county,'parcel_endpoint',parcel_endpoint,
  'identity_field',identity_field,'parcel_fields',parcel_fields,
  'road_endpoint',road_endpoint,'road_fields',road_fields,'row_endpoint',row_endpoint)
from (values
 ('lee','https://gismapserver.leegov.com/gisserver910/rest/services/DataExplorer/Parcels/MapServer/0/query',
  'STRAP','OBJECTID,STRAP,FOLIOID,LEGAL,FRONTAGE',
  'https://gismapserver.leegov.com/gisserver910/rest/services/Layers/RoadCenterlines/MapServer/2/query',
  'OBJECTID,STREET,SURFACE,MAINTAINBY,ROW_WIDTH',
  'https://gismapserver.leegov.com/gisserver910/rest/services/Layers/DOT_Engineering/MapServer/10/query'),
 ('charlotte','https://agis3.charlottecountyfl.gov/arcgis/rest/services/Essentials/CCGIS_BaseLayers/MapServer/5/query',
  'ACCOUNT','OBJECTID,ACCOUNT,shortlegal,roads,zoningcode,landuse',
  'https://agis3.charlottecountyfl.gov/arcgis/rest/services/Essentials/CCGIS_BaseLayers/MapServer/11/query',
  'OBJECTID,STREET,PAVED,MAINT_BY,MB_DESCRIPTION',null),
 ('marion','https://gis.marionfl.org/public/rest/services/General/Parcels/MapServer/0/query',
  'PARCEL','OBJECTID,PARCEL,ALT_Key',
  'https://gis.marionfl.org/public/rest/services/General/OCE_Roads/MapServer/9/query',
  'OBJECTID,Street,Paved,SurfaceType,Jurisdiction',null),
 ('highlands','https://gis.highlandsfl.gov/server/rest/services/CentralSquare/CSMapforPublic/FeatureServer/2/query',
  'STRAP','OBJECTID,STRAP,PARCELNO',
  'https://gis.highlandsfl.gov/server/rest/services/Layers/Road_Centerline/FeatureServer/0/query',
  'OBJECTID_1,FULLNAME,MAINT_CAT,MAINT_DESC',null),
 ('polk','https://gis.polk-county.net/server/rest/services/Map_Property_Appraiser/MapServer/1/query',
  'PARCELID','OBJECTID,PARCELID,PR_STRAP',
  'https://gis.polk-county.net/hosting/rest/services/All-In-One_Viewer/Roads_Map/MapServer/43/query',
  'OBJECTID,SURFTYPE,UNITDESC,OWN,SERVSTAT',null),
 ('osceola','https://paags.property-appraiser.org/server/rest/services/FabricParcel/MapServer/0/query',
  'PARCELID','OBJECTID,PARCELID,strap,dsp_strap',
  'https://services6.arcgis.com/9zKHLCgIwu2HFA5O/arcgis/rest/services/Centerlines/FeatureServer/0/query',
  'OBJECTID,STREET',null),
 ('sarasota','https://services3.arcgis.com/icrWMv7eBkctFu1f/arcgis/rest/services/ParcelHosted/FeatureServer/0/query',
  'ID','OBJECTID,ID,ACCOUNT',
  'https://services3.arcgis.com/icrWMv7eBkctFu1f/ArcGIS/rest/services/Cartegraph_Pavement_3_view/FeatureServer/0/query',
  'OBJECTID,STREET_NAME,Maintenance,SurfaceType,PavementClass',null)
) c(county,parcel_endpoint,identity_field,parcel_fields,road_endpoint,road_fields,row_endpoint)
where county=lower(trim(p_county))
$$;

create or replace function god_mode_ops.road_surface_class(p_county text,a jsonb)
returns text language sql immutable set search_path = '' as $$
select case lower(p_county)
 when 'lee' then case upper(trim(a->>'SURFACE')) when 'PAVED' then 'PAVED' when 'DIRT' then 'UNPAVED' when 'GRAVEL' then 'UNPAVED' else 'REVIEW' end
 when 'charlotte' then case upper(trim(a->>'PAVED')) when 'YES' then 'PAVED' when 'NO' then 'UNPAVED' else 'REVIEW' end
 when 'polk' then case upper(trim(a->>'SURFTYPE')) when 'PAV' then 'PAVED' when 'UNPAV' then 'UNPAVED' else 'REVIEW' end
 when 'highlands' then case a->>'MAINT_CAT' when '1' then 'PAVED' when '41' then 'PAVED' when '2' then 'UNPAVED' when '42' then 'UNPAVED' else 'REVIEW' end
 -- County-maintained data reported dubious city values: zero is unresolved here.
 when 'marion' then case a->>'Paved' when '1' then 'PAVED' else 'REVIEW' end
 when 'sarasota' then case upper(trim(a->>'SurfaceType')) when 'ASPHALTIC CONCRETE' then 'PAVED' when 'DIRT' then 'UNPAVED' else 'REVIEW' end
 else 'REVIEW' end
$$;

-- Fixed sources only; the HTTP transport retains existing bounds/validation.
create or replace function god_mode_ops.gis_endpoint_allowed(p_endpoint text)
returns boolean language sql immutable set search_path = '' as $$
select coalesce(p_endpoint in (
 'https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0/query',
 'https://services1.arcgis.com/9AYCAcYVeEGk3ZMt/arcgis/rest/services/Maintained_Roadways/FeatureServer/1/query',
 'https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query',
 'https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0/query'
) or exists (
 select 1 from unnest(array['lee','charlotte','marion','highlands','polk','sarasota','osceola']) c
 cross join lateral (select god_mode_ops.county_gis_config(c) cfg) v
 where p_endpoint in (cfg->>'parcel_endpoint',cfg->>'road_endpoint',cfg->>'row_endpoint')
),false)
$$;
revoke all on function god_mode_ops.county_gis_config(text),god_mode_ops.road_surface_class(text,jsonb),god_mode_ops.gis_endpoint_allowed(text) from public,anon,authenticated;
grant execute on function god_mode_ops.county_gis_config(text),god_mode_ops.road_surface_class(text,jsonb),god_mode_ops.gis_endpoint_allowed(text) to service_role;
