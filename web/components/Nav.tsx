"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Radar"],
  ["/pipeline", "Pipeline"],
  ["/backtest", "Backtest"],
  ["/agent-ops", "Agent Ops"],
  ["/portfolio", "Portfolio Stats"],
  ["/review", "Review Queue"],
  ["/about", "About"],
] as const;

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="nav" aria-label="Main">
      {LINKS.map(([href, label]) => {
        const active = href === "/" ? path === "/" || path.startsWith("/company") : path.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
