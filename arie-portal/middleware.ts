import { NextRequest, NextResponse } from "next/server";

const user = process.env.ARIE_BASIC_AUTH_USER;
const password = process.env.ARIE_BASIC_AUTH_PASSWORD;

function unauthorized() {
  return new NextResponse("Authentication required", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="ARIE Command Center"'
    }
  });
}

export function middleware(request: NextRequest) {
  if (!user || !password) {
    return NextResponse.next();
  }

  const header = request.headers.get("authorization");
  if (!header?.startsWith("Basic ")) {
    return unauthorized();
  }

  const decoded = atob(header.slice(6));
  const [candidateUser, ...passwordParts] = decoded.split(":");
  const candidatePassword = passwordParts.join(":");

  if (candidateUser !== user || candidatePassword !== password) {
    return unauthorized();
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]
};
