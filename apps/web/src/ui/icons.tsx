import {
  Activity,
  ArrowRight,
  Bot,
  Box,
  ChartNoAxesCombined,
  Check,
  ClipboardCheck,
  File,
  Image,
  Layers,
  LayoutDashboard,
  Menu,
  Plus,
  Search,
  Settings2,
  Sparkles,
  Video,
  X,
  type LucideIcon,
} from "lucide-react";

const icons: Record<string, LucideIcon> = {
  "Command Center": LayoutDashboard,
  Products: Box,
  Agents: Bot,
  Approvals: ClipboardCheck,
  Activity,
  Settings: Settings2,
  image: Image,
  video: Video,
  document: File,
  research: Search,
  concepts: Sparkles,
  production: Layers,
  performance: ChartNoAxesCombined,
  close: X,
  add: Plus,
  next: ArrowRight,
  menu: Menu,
  check: Check,
};

/** All interface icons are decorative SVGs; their controls supply accessible names. */
export function UIIcon({ name, size = 20 }: { name: string; size?: number }) {
  const Icon = icons[name] ?? Layers;
  return (
    <Icon
      size={size}
      strokeWidth={1.7}
      aria-hidden="true"
      focusable="false"
      className="ui-icon"
    />
  );
}
