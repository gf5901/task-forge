/**
 * Minimal typings for `sst` Resource — real SST apps use generated types from the infra package.
 * CI installs only the API workspace; this shim satisfies `tsc --noEmit` without the full `sst` SDK.
 */
declare module "sst" {
  export const Resource: Record<string, { value?: string } | undefined>;
}
