do $$ declare d jsonb;
begin
 d:=jsonb_build_object('parcel_id','P','current_owner','OWNER','county','Lee','signal_type','lis_pendens',
 'verification_status','VERIFIED','active',true,'released',false,'release_search_complete',true,
 'checked_at',now(),'release_checked_at',now(),'event_date',current_date-30,
 'source_url','https://www.leeclerk.org/','source_record_id','INSTRUMENT','record_sha256',repeat('a',64));
 if not god_mode_ops.distress_evidence_qualifies(d,'P','Lee','OWNER') then raise exception 'current verified lis pendens rejected'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||'{"release_search_complete":false}','P','Lee','OWNER') then raise exception 'unchecked release accepted'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||'{"released":true}','P','Lee','OWNER') then raise exception 'released adverse record accepted'; end if;
 if god_mode_ops.distress_evidence_qualifies(d,'P','Lee','NEW OWNER') then raise exception 'prior owner adverse record accepted'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||jsonb_build_object('checked_at',now()-interval '8 days'),'P','Lee','OWNER') then raise exception 'stale search accepted'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||'{"verification_status":"REVIEW_REQUIRED"}','P','Lee','OWNER') then raise exception 'candidate auto-approved'; end if;
 d:=d||jsonb_build_object('signal_type','tax_certificate_delinquency','amount_due',1000,'delinquent_since',current_date-interval '13 months');
 if not god_mode_ops.distress_evidence_qualifies(d,'P','Lee','OWNER') then raise exception 'verified older unpaid tax certificate rejected'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||jsonb_build_object('delinquent_since',current_date-interval '12 months'),'P','Lee','OWNER') then raise exception '12-month threshold not strict'; end if;
 if god_mode_ops.distress_evidence_qualifies(d||'{"amount_due":0}','P','Lee','OWNER') then raise exception 'paid tax certificate accepted'; end if;
 if god_mode_ops.distress_evidence_qualifies('{}','P','Lee','OWNER') then raise exception 'missing evidence accepted'; end if;
end $$;
begin;
do $$ declare d jsonb; result jsonb;
begin
 d:=jsonb_build_object('parcel_id','334426L3030160020','source_name','ATTOM','source_url','https://api.gateway.attomdata.com/',
 'record_sha256',repeat('b',64),'verification_status','VERIFIED','qualifying_distress',true);
 result:=god_mode_ops.stage_distress_candidate(d);
 if result->'distress_auto_approved' is distinct from 'false'::jsonb then raise exception 'intake approved distress'; end if;
 if (select evidence->>'verification_status' from god_mode_ops.distress_evidence where parcel_id=d->>'parcel_id' and source_name='ATTOM')<>'REVIEW_REQUIRED'
 then raise exception 'submitted verified claim preserved'; end if;
end $$;
rollback;
