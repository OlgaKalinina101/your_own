import { type NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

async function proxy(request: NextRequest, context: { params: { path: string[] } }) {
  const segments = context.params.path;
  const path = segments.join("/");
  const url = `${BACKEND}/api/${path}${request.nextUrl.search}`;

  try {
    const headers: Record<string, string> = {};
    request.headers.forEach((value, key) => {
      const k = key.toLowerCase();
      if (k !== "host" && k !== "connection" && k !== "transfer-encoding") {
        headers[key] = value;
      }
    });

    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    let body: ArrayBuffer | null = null;
    if (hasBody) {
      body = await request.arrayBuffer();
    }

    const resp = await fetch(url, {
      method: request.method,
      headers,
      body: body,
    });

    const responseHeaders = new Headers();
    resp.headers.forEach((value, key) => {
      const k = key.toLowerCase();
      if (k !== "transfer-encoding" && k !== "connection") {
        responseHeaders.set(key, value);
      }
    });

    return new Response(resp.body, {
      status: resp.status,
      headers: responseHeaders,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { proxy_error: true, url, method: request.method, detail: msg },
      { status: 502 },
    );
  }
}

export function GET(req: NextRequest, ctx: { params: { path: string[] } }) {
  return proxy(req, ctx);
}
export function POST(req: NextRequest, ctx: { params: { path: string[] } }) {
  return proxy(req, ctx);
}
export function PUT(req: NextRequest, ctx: { params: { path: string[] } }) {
  return proxy(req, ctx);
}
export function DELETE(req: NextRequest, ctx: { params: { path: string[] } }) {
  return proxy(req, ctx);
}
export function OPTIONS(req: NextRequest, ctx: { params: { path: string[] } }) {
  return proxy(req, ctx);
}
