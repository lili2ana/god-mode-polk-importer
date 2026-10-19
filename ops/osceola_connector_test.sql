do $$ declare cfg jsonb; p jsonb; r jsonb;
begin
 cfg:=god_mode_ops.county_gis_config('osceola');
 if cfg->>'identity_field'<>'PARCELID' then raise exception 'padded strap incorrectly used as identity'; end if;
 p:=god_mode_ops.gis_query(cfg->>'parcel_endpoint',jsonb_build_object('where','PARCELID=''052527409200011890''',
 'outFields',cfg->>'parcel_fields','returnGeometry','true','outSR','4326'));
 if p->'ok' is distinct from 'true'::jsonb or jsonb_array_length(p->'data'->'features')<>1
 or p->'data'->'features'->0->'attributes'->>'PARCELID'<>'052527409200011890'
 then raise exception 'Osceola exact polygon failed'; end if;
 r:=god_mode_ops.gis_query(cfg->>'road_endpoint',jsonb_build_object('where','1=1','geometry',
 (p->'data'->'features'->0->'geometry')::text,'geometryType','esriGeometryPolygon','inSR','4326','distance',30,
 'units','esriSRUnit_Meter','outFields',cfg->>'road_fields','returnGeometry','false'));
 if r->'ok' is distinct from 'true'::jsonb then raise exception 'Osceola road query failed'; end if;
 if god_mode_ops.road_surface_class('osceola','{"STREET":"test"}')<>'REVIEW' then raise exception 'missing pavement evidence approved'; end if;
end $$;
