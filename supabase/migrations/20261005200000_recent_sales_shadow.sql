-- Reduced shadow only: staging is incomplete, so no reader cutover or cleanup.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
CREATE TABLE public.polk_sales_recent_shadow (LIKE public.polk_sales_v2 INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
ALTER TABLE public.polk_sales_recent_shadow ADD PRIMARY KEY (parcel_id,ln_num);
ALTER TABLE public.polk_sales_recent_shadow ADD CONSTRAINT recent_sales_window CHECK (saledt >= DATE '2022-01-01');
ALTER TABLE public.polk_sales_recent_shadow ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.polk_sales_recent_shadow FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.polk_sales_recent_shadow TO service_role;
INSERT INTO public.polk_sales_recent_shadow (parcel_id,sale_id,ln_num,saledt,price,book,page,saletype,trns_cd,trns_dscr,instrtyp,instrtyp_dscr,grantor,grantee,foreclosure)
SELECT btrim(s.parcel_id),nullif(s.sale_id,''),btrim(s.ln_num)::integer,to_date(btrim(s.saledt),'MM/DD/YYYY'),nullif(btrim(s.price),'')::numeric,nullif(s.book,''),nullif(s.page,''),nullif(s.saletype,''),nullif(s.trns_cd,''),nullif(s.trns_dscr,''),nullif(s.instrtyp,''),nullif(s.instrtyp_dscr,''),nullif(s.grantor,''),nullif(s.grantee,''),nullif(s.foreclosure,'')
FROM public.polk_sales_stage_v2 s JOIN public.polk_parcel_v2 p ON p.parcel_id=btrim(s.parcel_id)
WHERE p.dordesc='RES' AND substring(btrim(s.saledt) from 7 for 4)>='2022'
AND to_date(btrim(s.saledt),'MM/DD/YYYY') <= CURRENT_DATE;
CREATE INDEX polk_sales_recent_shadow_date ON public.polk_sales_recent_shadow(saledt);
COMMENT ON TABLE public.polk_sales_recent_shadow IS 'Partial 2022+ residential sales from incomplete legacy staging. Shadow only; not verified source completeness, not an ARV feed. No cleanup or reader cutover authorized by this table.';
