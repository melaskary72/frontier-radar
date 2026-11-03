"use client";
import { useEffect, useRef, useState } from "react";

// Render charts at their real pixel width so text stays 11 to 12px on every screen.
export default function useWidth<T extends HTMLElement>(fallback = 720) {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}
