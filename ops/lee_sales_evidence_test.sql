do $$ declare s jsonb; a jsonb;
begin
 s:='{"STRAP":"subject","GISACRES":0.25,"ZONING":"RS-1","LONGITUDE":-81.7128,"LATITUDE":26.6026}';
 a:=jsonb_build_object('STRAP','comp','GISACRES',0.25,'ZONING','RS-1','LONGITUDE',-81.713,'LATITUDE',26.603,
 'S_1DATE',extract(epoch from now()-interval '30 days')*1000,'S_1AMOUNT',35000,'S_1VI','V','S_1TC','01','S_1TOC','01','S_1OR_NUM','123');
 if not god_mode_ops.lee_comp_usable(a,s) then raise exception 'valid nearby qualified land sale rejected'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"S_1TC":"05"}',s) then raise exception 'bulk-sale price accepted'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"S_1VI":"I"}',s) then raise exception 'improved sale accepted'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"LONGITUDE":-81.65}',s) then raise exception 'distant comp accepted'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"S_1DATE":null}',s) then raise exception 'missing date accepted'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"S_1TOC":"99"}',s) then raise exception 'pending transfer accepted'; end if;
 if god_mode_ops.lee_comp_usable(a||'{"ZONING":"MPD"}',s) then raise exception 'different zoning accepted'; end if;
end $$;
