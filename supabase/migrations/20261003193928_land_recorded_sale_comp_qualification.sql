create table if not exists god_mode_ops.dd_land_comp_qualification (
  dd_id uuid primary key references public.due_diligence_reviews(id) on delete cascade,
  property_id uuid not null references public.properties(id) on delete cascade,
  parcel_id text not null,
  subject_acreage numeric,
  status text not null check (status in ('qualified','review_required')),
  comp_count integer not null default 0 check (comp_count >= 0),
  p25_price_per_acre numeric,
  median_price_per_acre numeric,
  p75_price_per_acre numeric,
  iqr_ratio numeric,
  comp_estimated_value numeric,
  oldest_sale date,
  newest_sale date,
  evidence jsonb not null default '[]'::jsonb,
  methodology jsonb not null default '{}'::jsonb,
  evidence_hash text not null,
  refreshed_at timestamptz not null default now()
);

alter table god_mode_ops.dd_land_comp_qualification enable row level security;
revoke all on god_mode_ops.dd_land_comp_qualification from public, anon, authenticated;
grant select, insert, update, delete on god_mode_ops.dd_land_comp_qualification to service_role;

create or replace function god_mode_ops.refresh_land_comp_qualification()
returns jsonb
language plpgsql
security invoker
set search_path = pg_catalog, public, god_mode_ops, extensions
as $$
declare
  v_total integer;
  v_qualified integer;
begin
  delete from god_mode_ops.dd_land_comp_qualification;

  with targets as materialized (
    select
      dd.id as dd_id,
      p.id as property_id,
      p.parcel_id,
      nullif(regexp_replace(pv.tot_acreage,'[^0-9.]','','g'),'')::numeric as subject_acreage,
      pv.dorus_code,
      pv.nh_cd
    from public.due_diligence_reviews dd
    join public.properties p on p.id=dd.property_id
    join public.polk_parcel_v2 pv on pv.parcel_id=p.parcel_id
    where lower(coalesce(p.property_type,''))='land'
  ),
  candidate_pairs as materialized (
    select
      t.dd_id,
      t.property_id,
      t.parcel_id,
      t.subject_acreage,
      cp.parcel_id as comp_parcel_id,
      nullif(regexp_replace(cp.tot_acreage,'[^0-9.]','','g'),'')::numeric as comp_acreage
    from targets t
    join public.polk_parcel_v2 cp
      on cp.dorus_code=t.dorus_code
     and cp.nh_cd=t.nh_cd
     and cp.parcel_id<>t.parcel_id
  ),
  candidate_ids as materialized (
    select distinct comp_parcel_id from candidate_pairs
  ),
  parsed_sales as materialized (
    select
      s.parcel_id,
      s.sale_id,
      s.ln_num,
      case
        when s.saledt ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
        then to_date(s.saledt,'MM/DD/YYYY')
      end as sale_date,
      nullif(regexp_replace(s.price,'[^0-9.]','','g'),'')::numeric as sale_price,
      s.book,
      s.page,
      s.trns_cd,
      s.trns_dscr,
      s.instrtyp,
      s.instrtyp_dscr,
      s.foreclosure
    from public.polk_sales_stage_v2 s
    join candidate_ids ci on ci.comp_parcel_id=s.parcel_id
  ),
  qualified_sales as materialized (
    select distinct on (s.parcel_id)
      s.parcel_id,
      s.sale_id,
      s.ln_num,
      s.sale_date,
      s.sale_price,
      s.book,
      s.page,
      s.trns_cd,
      s.trns_dscr,
      s.instrtyp,
      s.instrtyp_dscr
    from parsed_sales s
    where s.sale_date >= current_date - interval '4 years'
      and s.sale_price >= 1000
      and coalesce(s.foreclosure,'N')='N'
      and (
        coalesce(s.instrtyp_dscr,'') ilike '%One Parcel Qualified%'
        or coalesce(s.instrtyp_dscr,'') ilike 'Q-Per examination of deed'
        or coalesce(s.instrtyp_dscr,'') ilike 'Q-Credible, verified & documented'
      )
      and coalesce(s.instrtyp_dscr,'') not ilike '%disqualified%'
      and coalesce(s.instrtyp_dscr,'') not ilike '%multiple parcel%'
    order by s.parcel_id, s.sale_date desc, s.sale_price desc, s.ln_num
  ),
  usable as materialized (
    select
      cp.dd_id,
      cp.property_id,
      cp.parcel_id,
      cp.subject_acreage,
      cp.comp_parcel_id,
      cp.comp_acreage,
      qs.sale_id,
      qs.ln_num,
      qs.sale_date,
      qs.sale_price,
      qs.book,
      qs.page,
      qs.trns_cd,
      qs.trns_dscr,
      qs.instrtyp,
      qs.instrtyp_dscr,
      qs.sale_price/cp.comp_acreage as price_per_acre
    from candidate_pairs cp
    join qualified_sales qs on qs.parcel_id=cp.comp_parcel_id
    where cp.subject_acreage > 0
      and cp.comp_acreage > 0
      and cp.comp_acreage between greatest(0.01,cp.subject_acreage*0.5) and cp.subject_acreage*1.5
  ),
  stats as materialized (
    select
      u.dd_id,
      count(*)::integer as comp_count,
      percentile_cont(0.25) within group(order by u.price_per_acre) as p25_ppa,
      percentile_cont(0.50) within group(order by u.price_per_acre) as median_ppa,
      percentile_cont(0.75) within group(order by u.price_per_acre) as p75_ppa,
      min(u.sale_date) as oldest_sale,
      max(u.sale_date) as newest_sale
    from usable u
    group by u.dd_id
  ),
  evidence as materialized (
    select
      u.dd_id,
      jsonb_agg(
        jsonb_build_object(
          'parcel_id',u.comp_parcel_id,
          'sale_id',u.sale_id,
          'line_number',u.ln_num,
          'sale_date',u.sale_date,
          'sale_price',u.sale_price,
          'acreage',u.comp_acreage,
          'price_per_acre',round(u.price_per_acre,2),
          'book',u.book,
          'page',u.page,
          'transaction_code',u.trns_cd,
          'transaction_description',u.trns_dscr,
          'instrument_type',u.instrtyp,
          'instrument_description',u.instrtyp_dscr
        )
        order by u.sale_date desc,u.comp_parcel_id
      ) as comp_evidence
    from usable u
    group by u.dd_id
  ),
  final as (
    select
      t.dd_id,
      t.property_id,
      t.parcel_id,
      t.subject_acreage,
      coalesce(s.comp_count,0) as comp_count,
      s.p25_ppa,
      s.median_ppa,
      s.p75_ppa,
      case
        when s.median_ppa > 0
        then (s.p75_ppa-s.p25_ppa)/s.median_ppa
      end as iqr_ratio,
      case
        when s.median_ppa > 0 and t.subject_acreage > 0
        then s.median_ppa*t.subject_acreage
      end as comp_estimated_value,
      s.oldest_sale,
      s.newest_sale,
      coalesce(e.comp_evidence,'[]'::jsonb) as comp_evidence
    from targets t
    left join stats s on s.dd_id=t.dd_id
    left join evidence e on e.dd_id=t.dd_id
  )
  insert into god_mode_ops.dd_land_comp_qualification(
    dd_id,property_id,parcel_id,subject_acreage,status,comp_count,
    p25_price_per_acre,median_price_per_acre,p75_price_per_acre,iqr_ratio,
    comp_estimated_value,oldest_sale,newest_sale,evidence,methodology,evidence_hash,refreshed_at
  )
  select
    f.dd_id,
    f.property_id,
    f.parcel_id,
    f.subject_acreage,
    case
      when f.comp_count >= 5
       and f.median_ppa > 0
       and f.iqr_ratio <= 1.0
       and f.newest_sale >= current_date - interval '18 months'
      then 'qualified'
      else 'review_required'
    end,
    f.comp_count,
    f.p25_ppa,
    f.median_ppa,
    f.p75_ppa,
    f.iqr_ratio,
    f.comp_estimated_value,
    f.oldest_sale,
    f.newest_sale,
    f.comp_evidence,
    jsonb_build_object(
      'version','land-ppa-v1',
      'source','Polk Property Appraiser recorded sales staging',
      'lookback_years',4,
      'minimum_unique_comps',5,
      'same_neighborhood',true,
      'same_dor_use_code',true,
      'acreage_ratio_min',0.5,
      'acreage_ratio_max',1.5,
      'foreclosure_excluded',true,
      'multi_parcel_excluded',true,
      'disqualified_instruments_excluded',true,
      'maximum_iqr_to_median_ratio',1.0,
      'maximum_newest_sale_age_months',18,
      'valuation_basis','median price per acre times subject acreage'
    ),
    encode(
      extensions.digest(
        convert_to(
          jsonb_build_object(
            'parcel_id',f.parcel_id,
            'subject_acreage',f.subject_acreage,
            'comp_count',f.comp_count,
            'p25_ppa',f.p25_ppa,
            'median_ppa',f.median_ppa,
            'p75_ppa',f.p75_ppa,
            'iqr_ratio',f.iqr_ratio,
            'comp_estimated_value',f.comp_estimated_value,
            'oldest_sale',f.oldest_sale,
            'newest_sale',f.newest_sale,
            'evidence',f.comp_evidence,
            'method_version','land-ppa-v1'
          )::text,
          'UTF8'
        ),
        'sha256'
      ),
      'hex'
    ),
    now()
  from final f;

  update public.due_diligence_reviews dd
  set
    comps_status = q.status,
    findings = jsonb_set(
      coalesce(dd.findings,'{}'::jsonb),
      '{comps}',
      jsonb_build_object(
        'status',q.status,
        'methodology',q.methodology,
        'subject_acreage',q.subject_acreage,
        'comp_count',q.comp_count,
        'p25_price_per_acre',q.p25_price_per_acre,
        'median_price_per_acre',q.median_price_per_acre,
        'p75_price_per_acre',q.p75_price_per_acre,
        'iqr_ratio',q.iqr_ratio,
        'estimated_value',q.comp_estimated_value,
        'oldest_sale',q.oldest_sale,
        'newest_sale',q.newest_sale,
        'evidence_hash',q.evidence_hash,
        'recorded_sales',q.evidence,
        'eligible_for_automated_underwriting',q.status='qualified'
      ),
      true
    ),
    updated_at=now()
  from god_mode_ops.dd_land_comp_qualification q
  where dd.id=q.dd_id;

  select count(*),count(*) filter(where status='qualified')
  into v_total,v_qualified
  from god_mode_ops.dd_land_comp_qualification;

  return jsonb_build_object(
    'processed',v_total,
    'qualified',v_qualified,
    'review_required',v_total-v_qualified,
    'method_version','land-ppa-v1',
    'ran_at',now()
  );
end;
$$;

revoke all on function god_mode_ops.refresh_land_comp_qualification() from public,anon,authenticated;
grant execute on function god_mode_ops.refresh_land_comp_qualification() to service_role;

do $$
begin
  if not exists (select 1 from cron.job where jobname='god_mode_land_comp_qualify') then
    perform cron.schedule(
      'god_mode_land_comp_qualify',
      '35 6 * * *',
      'select god_mode_ops.refresh_land_comp_qualification();'
    );
  end if;
end
$$;
;
