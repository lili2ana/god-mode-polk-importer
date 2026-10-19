-- Private, bounded county/FEMA/NWI reads. No machine token or public RPC.
create or replace function god_mode_ops.gis_response_complete(p_data jsonb)
returns boolean language sql immutable set search_path = '' as $$
  select coalesce(jsonb_typeof(p_data) = 'object'
    and not (p_data ? 'error')
    and coalesce(p_data->>'exceededTransferLimit', 'false') = 'false'
    and jsonb_typeof(p_data->'features') = 'array', false)
$$;

create or replace function god_mode_ops.gis_query(p_endpoint text, p_params jsonb)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare response extensions.http_response; data jsonb; attempt integer;
  failures jsonb := '[]';
begin
  if current_user not in ('postgres','service_role') then
    raise exception 'backend only';
  end if;
  if not god_mode_ops.gis_endpoint_allowed(p_endpoint)
    or jsonb_typeof(p_params) is distinct from 'object' then
    raise exception 'invalid GIS request';
  end if;
  perform set_config('http.curlopt_timeout_ms','8000',true);
  perform set_config('http.curlopt_connecttimeout_ms','3000',true);
  for attempt in 1..2 loop
    begin
      -- Hosted ArcGIS services reject some long polygon GET URLs (observed 404).
      if length(extensions.urlencode(p_params))>1500 then
        response := extensions.http_post(p_endpoint, p_params || '{"f":"json"}'::jsonb);
      else
        response := extensions.http_get(p_endpoint, p_params || '{"f":"json"}'::jsonb);
      end if;
      if response.status = 200 then
        data := response.content::jsonb;
        if god_mode_ops.gis_response_complete(data) then
          return jsonb_build_object('ok',true,'data',data,'attempts',attempt,
            'http_status',response.status,'failures',failures);
        end if;
        -- Malformed, truncated and ArcGIS error responses never mean absence.
        return jsonb_build_object('ok',false,'kind','incomplete_or_arcgis_error',
          'http_status',response.status,'attempts',attempt);
      end if;
      failures := failures || jsonb_build_array(jsonb_build_object(
        'kind','http','http_status',response.status));
      if response.status not in (429,500,502,503,504) then exit; end if;
    exception when others then
      -- Persist diagnostic category only, never arbitrary remote bodies or credentials.
      failures := failures || jsonb_build_array(jsonb_build_object('kind','transport_or_json','sqlstate',sqlstate));
    end;
  end loop;
  return jsonb_build_object('ok',false,'attempts',jsonb_array_length(failures),'failures',failures);
end $$;
revoke all on function god_mode_ops.gis_response_complete(jsonb) from public,anon,authenticated;
revoke all on function god_mode_ops.gis_query(text,jsonb) from public,anon,authenticated;
grant execute on function god_mode_ops.gis_response_complete(jsonb), god_mode_ops.gis_query(text,jsonb) to service_role;
