/** Provider-neutral contract returned by platform health endpoints. */
export interface HealthResponse {
  readonly status: "ok";
  readonly service: string;
}

export type { components, operations, paths } from "./generated";
