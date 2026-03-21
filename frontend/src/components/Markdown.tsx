import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkBreaks from "remark-breaks"

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-custom">
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        pre({ children }) {
          return <pre className="overflow-x-auto rounded-md bg-zinc-900 p-3 text-[13px]">{children}</pre>
        },
        code({ className, children, ...props }) {
          const isBlock = className?.startsWith("language-")
          if (isBlock) {
            return <code className="text-zinc-200 font-mono text-[13px] leading-relaxed" {...props}>{children}</code>
          }
          return (
            <code className="rounded bg-zinc-800 px-1.5 py-0.5 text-[13px] text-zinc-300 font-mono" {...props}>
              {children}
            </code>
          )
        },
        a({ href, children }) {
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-indigo-400 hover:text-indigo-300 underline underline-offset-2">
              {children}
            </a>
          )
        },
        table({ children }) {
          return (
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">{children}</table>
            </div>
          )
        },
        th({ children }) {
          return <th className="border-b border-zinc-700 px-3 py-1.5 text-left text-xs font-medium text-zinc-400">{children}</th>
        },
        td({ children }) {
          return <td className="border-b border-zinc-800/50 px-3 py-1.5 text-zinc-300">{children}</td>
        },
      }}
    >
      {children}
    </ReactMarkdown>
    </div>
  )
}
