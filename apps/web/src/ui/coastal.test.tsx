import { readFileSync } from "node:fs";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { catalogApi, type Asset } from "../catalog-api";
import { Overlay } from "./overlay";
import { AssetDrawer } from "./asset-drawer";

const session = { tenantId: "test-tenant", credential: "test-only" };
const asset: Asset = {
  id: "test-asset",
  tenant_id: session.tenantId,
  brand_id: "test-brand",
  product_id: "test-product",
  kind: "image",
  role: "product_detail",
  origin: "upload",
  original_filename: "original.png",
  declared_mime_type: "image/png",
  detected_mime_type: "image/png",
  byte_size: 1234,
  digest: "immutable-digest",
  width: 640,
  height: 480,
  duration_ms: null,
  rights_status: "confirmed",
  allowed_uses: ["internal_analysis"],
  status: "ready",
  can_edit: false,
  parent_asset_id: null,
  source_url: null,
  rejection_code: null,
  created_by: "test-actor",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const grant = () => ({
  url: "https://media.example.test/original.png?signature=test-only",
  expires_at: new Date(Date.now() + 60000).toISOString(),
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("coastal design contracts", () => {
  it.each(["creative-manager-mark.svg", "creative-manager-app-icon.svg"])(
    "ships %s as transparent vector paths, not a raster wrapper",
    (name) => {
      const source = readFileSync(`public/brand/${name}`, "utf8");
      const svg = new DOMParser().parseFromString(source, "image/svg+xml");
      expect(svg.querySelector("parsererror")).toBeNull();
      expect(svg.querySelector("svg[viewBox]")).not.toBeNull();
      expect(svg.querySelectorAll("path").length).toBeGreaterThan(1);
      expect(
        svg.querySelector("image, script, foreignObject, rect"),
      ).toBeNull();
      expect(source).not.toMatch(/data:image|base64|onload=|href=/);
    },
  );

  it("keeps text/action contrast readable and eliminates purple brand values", () => {
    const css = readFileSync("src/ui/tokens.css", "utf8");
    const token = (key: string) =>
      css.match(new RegExp(`--${key}: (#[a-f0-9]{6})`))![1]!;
    const luminance = (hex: string) => {
      const rgb = [1, 3, 5]
        .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
        .map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
      return rgb[0]! * 0.2126 + rgb[1]! * 0.7152 + rgb[2]! * 0.0722;
    };
    const contrast = (a: string, b: string) =>
      (Math.max(luminance(a), luminance(b)) + 0.05) /
      (Math.min(luminance(a), luminance(b)) + 0.05);
    expect(contrast(token("action"), "#ffffff")).toBeGreaterThanOrEqual(4.5);
    for (const surface of [
      "sky",
      "mint",
      "turquoise",
      "surface",
      "background",
    ]) {
      expect(
        contrast(token("text-primary"), token(surface)),
      ).toBeGreaterThanOrEqual(4.5);
    }
    expect(token("brand-purple")).toBe(token("turquoise"));
    expect(token("brand-magenta")).toBe(token("mint"));
    const layout = readFileSync("app/layout.tsx", "utf8");
    expect(layout).toContain("creative-manager-app-icon.svg");
    expect(layout).not.toContain(".png");
  });
});

describe("accessible shared overlays", () => {
  it("honors initial focus, contains Tab, restores the opener and scroll state", () => {
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    document.documentElement.style.overflow = "auto";
    const { unmount } = render(
      <Overlay title="Test details" onClose={vi.fn()}>
        <input aria-label="Title" data-initial-focus />
        <button>Last action</button>
      </Overlay>,
    );
    expect(screen.getByLabelText("Title")).toHaveFocus();
    expect(document.documentElement.style.overflow).toBe("hidden");
    const close = screen.getByRole("button", { name: "Close Test details" });
    const last = screen.getByRole("button", { name: "Last action" });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    unmount();
    expect(opener).toHaveFocus();
    expect(document.documentElement.style.overflow).toBe("auto");
    opener.remove();
    document.documentElement.style.overflow = "";
  });

  it("supports Escape, close and backdrop, but refuses them while saving", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Overlay title="Test" onClose={onClose}>
        <p>Detail</p>
      </Overlay>,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close Test" }));
    fireEvent.click(screen.getByRole("dialog"), { clientX: -1 });
    expect(onClose).toHaveBeenCalledTimes(3);
    rerender(
      <Overlay title="Test" onClose={onClose} busy>
        <p>Saving</p>
      </Overlay>,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close Test" }));
    expect(onClose).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("button", { name: "Close Test" })).toBeDisabled();
  });
});

describe("immutable Asset preview", () => {
  it("loads the original grant only; does not archive, upload, finalize or start generation", async () => {
    const original = JSON.stringify(asset);
    const download = vi
      .spyOn(catalogApi, "downloadAsset")
      .mockResolvedValue(grant());
    const writes = [
      vi.spyOn(catalogApi, "archiveAsset"),
      vi.spyOn(catalogApi, "createAsset"),
      vi.spyOn(catalogApi, "finalizeAsset"),
      vi.spyOn(catalogApi, "startCreativeStrategist"),
    ];
    render(<AssetDrawer asset={asset} session={session} onClose={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-busy", "true");
    const image = await screen.findByRole("img", {
      name: asset.original_filename,
    });
    expect(image).toHaveAttribute("src", grant().url);
    expect(image).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(download).toHaveBeenCalledExactlyOnceWith(session, asset.id);
    writes.forEach((write) => expect(write).not.toHaveBeenCalled());
    expect(JSON.stringify(asset)).toBe(original);
  });

  it("shows a safe error and retries only the read grant", async () => {
    const download = vi
      .spyOn(catalogApi, "downloadAsset")
      .mockRejectedValueOnce(new Error("private-token"));
    render(<AssetDrawer asset={asset} session={session} onClose={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "original asset is unchanged",
    );
    expect(document.body.textContent).not.toContain("private-token");
    download.mockResolvedValue(grant());
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("img");
    expect(download).toHaveBeenCalledTimes(2);
  });

  it.each(["javascript:alert(1)", "data:image/svg+xml,<svg />"])(
    "rejects an unsafe preview URL: %s",
    async (url) => {
      vi.spyOn(catalogApi, "downloadAsset").mockResolvedValue({
        ...grant(),
        url,
      });
      render(<AssetDrawer asset={asset} session={session} onClose={vi.fn()} />);
      await screen.findByRole("alert");
      expect(screen.queryByRole("img")).toBeNull();
    },
  );

  it("removes expired grants and does not fetch a replacement automatically", async () => {
    vi.useFakeTimers();
    const download = vi
      .spyOn(catalogApi, "downloadAsset")
      .mockResolvedValue(grant());
    render(<AssetDrawer asset={asset} session={session} onClose={vi.fn()} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("img")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(60001);
    });
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("alert")).toHaveTextContent("preview expired");
    expect(download).toHaveBeenCalledOnce();
  });

  it("uses a native video player without autoplay", async () => {
    vi.spyOn(catalogApi, "downloadAsset").mockResolvedValue(grant());
    const { unmount } = render(
      <AssetDrawer
        asset={{ ...asset, kind: "video" }}
        session={session}
        onClose={vi.fn()}
      />,
    );
    await waitFor(() => expect(document.querySelector("video")).not.toBeNull());
    expect(document.querySelector("video")).toHaveAttribute("controls");
    expect(document.querySelector("video")).not.toHaveAttribute("autoplay");
    unmount();
    expect(document.querySelector("video")).toBeNull();
  });

  it("ignores pending grant completion after closing", async () => {
    let resolve!: (value: ReturnType<typeof grant>) => void;
    vi.spyOn(catalogApi, "downloadAsset").mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const { unmount } = render(
      <AssetDrawer asset={asset} session={session} onClose={vi.fn()} />,
    );
    unmount();
    await act(async () => {
      resolve(grant());
    });
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
