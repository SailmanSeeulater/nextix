import type { Metadata } from "next";
import { CostsView } from "@/components/CostsView";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Costs · nexTix",
};

/** What agent runs cost (docs/phase6.md). The browser asks /api/costs for the chosen window. */
export default function CostsPage() {
  return (
    <main>
      <CostsView />
    </main>
  );
}
