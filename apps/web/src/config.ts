import { z } from "zod";

const publicConfigSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.url(),
});

export interface PublicConfig {
  readonly apiBaseUrl: string;
}

export function getPublicConfig(
  environment: Record<string, string | undefined> = process.env,
): PublicConfig {
  const parsed = publicConfigSchema.parse(environment);
  return { apiBaseUrl: parsed.NEXT_PUBLIC_API_BASE_URL };
}
