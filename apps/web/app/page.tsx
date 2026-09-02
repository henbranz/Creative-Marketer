import { getPublicConfig } from "../src/config";

export default function Home() {
  const config = getPublicConfig();

  return (
    <main>
      <p className="eyebrow">Foundation Phase 0</p>
      <h1>Creative Marketer</h1>
      <p className="lede">
        The platform foundation is running. Domain agents will be added only
        after governance, tenancy, and security boundaries are in place.
      </p>
      <a href={`${config.apiBaseUrl}/health`}>API health</a>
    </main>
  );
}
