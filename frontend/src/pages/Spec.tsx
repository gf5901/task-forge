import specContent from "@/content/spec.md?raw"
import Markdown from "@/components/Markdown"
import { FileText } from "lucide-react"

export default function Spec() {
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-6 pb-16 space-y-6">
      <div className="flex items-center gap-2.5 border-b border-zinc-800/60 pb-5">
        <FileText className="size-4 text-zinc-500 shrink-0" />
        <h1 className="text-lg font-semibold text-zinc-100">Product Spec</h1>
      </div>
      <div className="prose prose-sm prose-zinc prose-invert max-w-none
        prose-headings:font-semibold prose-headings:text-zinc-100
        prose-h1:text-xl prose-h2:text-base prose-h2:mt-8 prose-h2:mb-3
        prose-h3:text-sm prose-h3:mt-6 prose-h3:mb-2
        prose-p:text-zinc-300 prose-p:leading-relaxed
        prose-li:text-zinc-300
        prose-strong:text-zinc-200
        prose-a:text-indigo-400 hover:prose-a:text-indigo-300
        prose-code:text-zinc-300 prose-code:bg-zinc-800/60 prose-code:px-1 prose-code:rounded prose-code:text-xs
        prose-pre:bg-zinc-900/60 prose-pre:border prose-pre:border-zinc-800/60
        prose-table:text-sm
        prose-thead:border-zinc-700 prose-th:text-zinc-400 prose-th:font-medium prose-th:py-2 prose-th:px-3
        prose-td:text-zinc-300 prose-td:py-2 prose-td:px-3
        prose-tr:border-zinc-800/60
        prose-hr:border-zinc-800/60
        prose-blockquote:border-zinc-700 prose-blockquote:text-zinc-400">
        <Markdown>{specContent}</Markdown>
      </div>
    </div>
  )
}
