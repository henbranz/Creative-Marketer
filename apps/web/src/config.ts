import { z } from "zod";

const publicConfigSchema = z.object({
  NEXT_PUBLIC_API_BASE_URL: z.url(),
  NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: z.preprocess(
    (value) =>
      typeof value === "string" && value.trim() === "" ? undefined : value,
    z.string().trim().min(1).max(200).optional(),
  ),
});

const nextPublicEnvironment = {
  NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  NEXT_PUBLIC_OBSIDIAN_VAULT_NAME: process.env.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME,
};

export interface PublicConfig {
  readonly apiBaseUrl: string;
  readonly obsidianVaultName: string | undefined;
}

export function getPublicConfig(
  environment: Record<string, string | undefined> = nextPublicEnvironment,
): PublicConfig {
  const parsed = publicConfigSchema.parse(environment);
  return {
    apiBaseUrl: parsed.NEXT_PUBLIC_API_BASE_URL,
    obsidianVaultName: parsed.NEXT_PUBLIC_OBSIDIAN_VAULT_NAME,
  };
}
