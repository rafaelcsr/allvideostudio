"use client";
import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { supabase } from "./supabase";

/** Returns the session; redirects to the login page when signed out (if `required`). */
export function useSession(required = true) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const sb = supabase();
    sb.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
      if (required && !data.session) window.location.href = "/";
    });
    const { data: sub } = sb.auth.onAuthStateChange((_e, s) => {
      setSession(s);
      if (required && !s) window.location.href = "/";
    });
    return () => sub.subscription.unsubscribe();
  }, [required]);

  return { session, ready };
}
