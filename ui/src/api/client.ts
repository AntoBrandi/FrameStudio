import createFetchClient, { type Middleware } from "openapi-fetch";
import createClient from "openapi-react-query";
import { useAuthStore } from "@/features/auth/store";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];
export type JobOut = Schemas["JobOut"];
export type GenerationOut = Schemas["GenerationOut"];
export type GenerationCreate = Schemas["GenerationCreate"];
export type AssetOut = Schemas["AssetOut"];
export type AppSettings = Schemas["AppSettings"];
export type AppSettingsPatch = Schemas["AppSettingsPatch"];
export type SystemInfo = Schemas["SystemInfo"];

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly code?: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

const authWatch: Middleware = {
  onResponse({ response }) {
    if (response.status === 401) useAuthStore.getState().setNeedsToken(true);
    return response;
  },
};

/** Same-origin client: the backend (or the dev proxy) serves the UI and the API. */
export const fetchClient = createFetchClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  credentials: "same-origin",
});
fetchClient.use(authWatch);

export const $api = createClient(fetchClient);

/** Turn an openapi-fetch error body into a readable message. */
export function errorMessage(error: unknown): string {
  if (!error) return "Unknown error";
  if (error instanceof Error) return error.message;
  if (typeof error === "object" && error !== null && "detail" in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item === "object" && item && "msg" in item ? String(item.msg) : ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return String(error);
}
