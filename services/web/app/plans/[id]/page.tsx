import { use } from "react";
import DagGraph from "@/components/DagGraph";

export default function PlanPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <h1 className="text-lg font-semibold text-slate-800 mb-4">
        DAG 视图 <span className="text-sm font-mono text-slate-400">{id}</span>
      </h1>
      <DagGraph planId={id} />
    </div>
  );
}
