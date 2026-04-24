"use client";

import { useRouter } from "next/navigation";
import { DashboardCard } from "@/components/DashboardCard";

export default function Dashboard() {
  const router = useRouter();

  return (
    <div className="relative flex h-screen w-screen items-center justify-center bg-black px-10 py-8">

      {/* Chat button — top right corner */}
      <button
        onClick={() => router.push("/chat")}
        className="absolute right-10 top-8 text-[0.68rem] tracking-[0.2em] uppercase text-white/40 transition-colors duration-300 hover:text-white/90"
      >
        chat →
      </button>
      {/*
        8 columns × 3 rows, replicating the sketch layout.

        Row 1:  Soul(1-3)  |  connector(4)  |  Skills(5-8)
        Row 2:  –          |  Memory(2-4)   |  sq(5) | sq(6) | Body(7-8)
        Row 3:  Voice(1-2) |                |                 | Settings(6-8)

        Connector cell in row 1 is split into two stacked mini-squares.
      */}
      <div
        className="grid w-full max-w-[1320px]"
        style={{
          gridTemplateColumns: "repeat(8, 1fr)",
          gridTemplateRows: "260px 220px 220px",
          gap: "10px",
        }}
      >
        {/* ── Row 1 ──────────────────────────────────────────────────────── */}

        {/* Soul */}
        <DashboardCard
          title="Soul"
          className="col-start-1 col-end-4 row-start-1 row-end-2"
          delay={80}
          href="/dashboard/soul"
        />

        {/* Connector: two stacked mini-squares */}
        <div
          className="col-start-4 col-end-5 row-start-1 row-end-2 flex flex-col"
          style={{ gap: "10px" }}
        >
          <div className="anim-card flex-1 border border-white/20 bg-black transition-colors duration-500 hover:border-white/50 hover:bg-white/[0.025]" style={{ animationDelay: "140ms" }} />
          <div className="anim-card flex-1 border border-white/20 bg-black transition-colors duration-500 hover:border-white/50 hover:bg-white/[0.025]" style={{ animationDelay: "180ms" }} />
        </div>

        {/* Skills */}
        <DashboardCard
          title="Skills"
          className="col-start-5 col-end-9 row-start-1 row-end-2"
          delay={120}
          href="/dashboard/skills"
        />

        {/* ── Row 2 ──────────────────────────────────────────────────────── */}

        {/* Memory */}
        <DashboardCard
          title="Memory"
          className="col-start-2 col-end-5 row-start-2 row-end-3"
          delay={200}
          href="/dashboard/memory"
        />

        {/* Small square left */}
        <div
          className="anim-card col-start-5 col-end-6 row-start-2 row-end-3 border border-white/20 bg-black transition-colors duration-500 hover:border-white/50 hover:bg-white/[0.025]"
          style={{ animationDelay: "240ms" }}
        />

        {/* Small square right */}
        <div
          className="anim-card col-start-6 col-end-7 row-start-2 row-end-3 border border-white/20 bg-black transition-colors duration-500 hover:border-white/50 hover:bg-white/[0.025]"
          style={{ animationDelay: "260ms" }}
        />

        {/* Body */}
        <DashboardCard
          title="Body"
          className="col-start-7 col-end-9 row-start-2 row-end-3"
          delay={280}
          href="/dashboard/body"
        />

        {/* ── Row 3 ──────────────────────────────────────────────────────── */}

        {/* Voice */}
        <DashboardCard
          title="Voice"
          subtitle="coming soon"
          className="col-start-1 col-end-3 row-start-3 row-end-4"
          delay={320}
        />

        {/* Settings */}
        <DashboardCard
          title="Settings"
          className="col-start-6 col-end-9 row-start-3 row-end-4"
          delay={360}
          href="/dashboard/settings"
        />
      </div>
    </div>
  );
}
