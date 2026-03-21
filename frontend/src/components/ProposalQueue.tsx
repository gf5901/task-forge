import { useState, useEffect, useCallback, useRef } from "react"
import { Link } from "react-router-dom"
import toast from "react-hot-toast"
import {
  Check,
  X,
  ChevronDown,
  ChevronUp,
  Loader,
  ExternalLink,
  MessageSquare,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import {
  fetchProposals,
  approveProposal,
  rejectProposal,
} from "@/lib/api"
import type { Proposal } from "@/lib/api"
import { timeAgo } from "@/lib/time"

const DOMAIN_BADGE: Record<string, string> = {
  code: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  content: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  seo: "bg-amber-500/15 text-amber-400 border-amber-500/20",
  outreach: "bg-violet-500/15 text-violet-400 border-violet-500/20",
  research: "bg-cyan-500/15 text-cyan-400 border-cyan-500/20",
}

const STATUS_BADGE: Record<string, string> = {
  pending: "bg-zinc-500/15 text-zinc-400 border-zinc-500/20",
  approved: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  rejected: "bg-red-500/15 text-red-400 border-red-500/20",
}

function ProposalCard({
  proposal,
  projectId,
  onUpdate,
}: {
  proposal: Proposal
  projectId: string
  onUpdate: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [feedback, setFeedback] = useState("")
  const [busy, setBusy] = useState(false)

  async function handleApprove() {
    setBusy(true)
    try {
      const res = await approveProposal(projectId, proposal.sk)
      toast.success(
        <span>
          Approved — task{" "}
          <Link to={`/tasks/${res.task_id}`} className="underline">
            {res.task_id}
          </Link>{" "}
          created
        </span>,
      )
      onUpdate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed")
    } finally {
      setBusy(false)
    }
  }

  async function handleReject() {
    setBusy(true)
    try {
      await rejectProposal(projectId, proposal.sk, feedback)
      toast.success("Proposal rejected")
      setRejecting(false)
      onUpdate()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-lg border border-zinc-800/60 bg-zinc-900/30 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-[13px] text-zinc-200">{proposal.action}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {proposal.domain && (
              <Badge
                variant="outline"
                className={`text-[10px] ${DOMAIN_BADGE[proposal.domain] ?? DOMAIN_BADGE.code}`}
              >
                {proposal.domain}
              </Badge>
            )}
            {proposal.target_kpi && (
              <span className="text-[11px] text-zinc-500">→ {proposal.target_kpi}</span>
            )}
            <span className="text-[11px] text-zinc-600">{timeAgo(proposal.created_at)}</span>
            {proposal.status !== "pending" && (
              <Badge
                variant="outline"
                className={`text-[10px] ${STATUS_BADGE[proposal.status] ?? ""}`}
              >
                {proposal.status}
              </Badge>
            )}
          </div>
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-0.5 shrink-0 text-zinc-500 hover:text-zinc-300"
        >
          {expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </button>
      </div>

      {expanded && (
        <div className="mt-3 border-t border-zinc-800/40 pt-3 text-[12px] text-zinc-400">
          <p className="mb-2">
            <span className="text-zinc-500">Rationale:</span> {proposal.rationale}
          </p>
          {proposal.task_id && (
            <p className="mb-2 flex items-center gap-1">
              <ExternalLink className="size-3" />
              <Link to={`/tasks/${proposal.task_id}`} className="text-indigo-400 hover:underline">
                Task {proposal.task_id}
              </Link>
            </p>
          )}
          {proposal.feedback && (
            <p className="mb-2">
              <span className="text-zinc-500">Feedback:</span> {proposal.feedback}
            </p>
          )}
          {proposal.outcome && (
            <p className="mb-2">
              <span className="text-zinc-500">Outcome:</span> {proposal.outcome}
            </p>
          )}
        </div>
      )}

      {proposal.status === "pending" && (
        <div className="mt-3 flex items-center gap-2">
          {rejecting ? (
            <div className="flex flex-1 flex-col gap-2">
              <Textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Why reject? (optional feedback for the agent)"
                rows={2}
                className="bg-zinc-900/50 border-zinc-800 text-[12px]"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-7 gap-1 text-[11px]"
                  disabled={busy}
                  onClick={() => void handleReject()}
                >
                  {busy ? <Loader className="size-3 animate-spin" /> : <X className="size-3" />}
                  Confirm reject
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 text-[11px]"
                  onClick={() => setRejecting(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <Button
                size="sm"
                className="h-7 gap-1 text-[11px] bg-emerald-600 hover:bg-emerald-700"
                disabled={busy}
                onClick={() => void handleApprove()}
              >
                {busy ? <Loader className="size-3 animate-spin" /> : <Check className="size-3" />}
                Approve
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1 text-[11px] text-red-400 hover:text-red-300"
                disabled={busy}
                onClick={() => setRejecting(true)}
              >
                <X className="size-3" />
                Reject
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function ProposalQueue({ projectId }: { projectId: string }) {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [filter, setFilter] = useState<string>("pending")
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    fetchProposals(projectId, filter === "all" ? undefined : filter)
      .then((p) => {
        setProposals(p.proposals)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [projectId, filter])

  const loadRef = useRef(load)
  useEffect(() => { loadRef.current = load })
  useEffect(() => { loadRef.current() }, [projectId, filter])

  const pendingCount = proposals.filter((p) => p.status === "pending").length

  return (
    <>
      {/* Proposals */}
      <section className="mb-8">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[13px] font-medium uppercase tracking-wide text-zinc-500">
            <MessageSquare className="size-4" />
            Proposals
            {pendingCount > 0 && filter !== "pending" && (
              <Badge variant="outline" className="ml-1 text-[10px] bg-indigo-500/15 text-indigo-400 border-indigo-500/20">
                {pendingCount} pending
              </Badge>
            )}
          </h2>
          <div className="flex gap-1">
            {["pending", "approved", "rejected", "all"].map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`rounded px-2 py-0.5 text-[11px] transition ${
                  filter === f
                    ? "bg-zinc-800 text-zinc-200"
                    : "text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <Loader className="size-4 animate-spin text-zinc-500" />
          </div>
        ) : proposals.length === 0 ? (
          <p className="text-[13px] text-zinc-500">No {filter !== "all" ? filter : ""} proposals yet.</p>
        ) : (
          <div className="space-y-2">
            {proposals.map((p) => (
              <ProposalCard
                key={p.sk}
                proposal={p}
                projectId={projectId}
                onUpdate={load}
              />
            ))}
          </div>
        )}
      </section>
    </>
  )
}
