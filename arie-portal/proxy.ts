import { NextRequest, NextResponse } from "next/server";

const user = process.env.ARIE_BASIC_AUTH_USER;
const password = process.env.ARIE_BASIC_AUTH_PASSWORD;

function unauthorized(message = "Authentication required") {
  return new NextResponse(message, {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="ARIE Internal Command Center", charset="UTF-8"',
      "Cache-Control": "no-store"
    }
  });
}

export function proxy(request: NextRequest) {
  if (!user || !password) {
    if (process.env.VERCEL_ENV === "production" || process.env.NODE_ENV === "production") {
      return new NextResponse("Command Center authentication is not configured.", {
        status: 503,
        headers: { "Cache-Control": "no-store" }
      });
    }
    return NextResponse.next();
  }

  const header = request.headers.get("authorization");
  if (!header?.startsWith("Basic ")) return unauthorized();

  try {
    const decoded = atob(header.slice(6));
    const separator = decoded.indexOf(":");
    const candidateUser = separator >= 0 ? decoded.slice(0, separator) : decoded;
    const candidatePassword = separator >= 0 ? decoded.slice(separator + 1) : "";
    if (candidateUser !== user || candidatePassword !== password) return unauthorized("Invalid credentials");
  } catch {
    return unauthorized("Invalid authorization header");
  }

  const response = NextResponse.next();
  response.headers.set("Cache-Control", "private, no-store");
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]
};
