import { NextResponse, type NextRequest } from "next/server";

// Access code gate. Enforced only when ACCESS_CODE is set (deployed preview); open locally.
export function middleware(req: NextRequest) {
  const code = process.env.ACCESS_CODE;
  if (!code) return NextResponse.next();
  const { pathname, searchParams } = req.nextUrl;
  if (pathname.startsWith("/access") || pathname.startsWith("/_next") || pathname === "/favicon.ico" ||
      pathname.startsWith("/icon") || pathname.startsWith("/apple-icon")) {
    return NextResponse.next();
  }
  const given = searchParams.get("code");
  if (given === code) {
    const url = req.nextUrl.clone();
    url.searchParams.delete("code");
    const res = NextResponse.redirect(url);
    res.cookies.set("fr_access", code, { httpOnly: true, sameSite: "lax", secure: true, maxAge: 60 * 60 * 24 * 30 });
    return res;
  }
  if (req.cookies.get("fr_access")?.value === code) return NextResponse.next();
  const url = req.nextUrl.clone();
  url.pathname = "/access";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/((?!_next/static|_next/image).*)"] };
