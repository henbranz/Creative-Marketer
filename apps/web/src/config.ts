import { z } from "zod";

const publicConfigSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.url(),
  NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: z.string().trim().min(1).max(200).optional(),
});

export interface PublicConfig {
  readonly apiBaseUrl: string;
  readonly obsidianVaultName: string | undefined;
}

export function getPublicConfig(
  environment: Record<string, string | undefined> = process.env,
): PublicConfig {
  const parsed = publicConfigSchema.parse(environment);
  return {
    apiBaseUrl: parsed.NEXT_PUBLIC_API_BASE_URL,
    obsidianVaultName: parsed.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME,
  };
}
