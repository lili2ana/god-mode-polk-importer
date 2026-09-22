import { createClient } from "https://esm.sh/@supabase/supabase-js@2.57.4";
import { buildHandler } from "./handler.ts";
Deno.serve(buildHandler({ env: (name) => Deno.env.get(name), createClient }));
