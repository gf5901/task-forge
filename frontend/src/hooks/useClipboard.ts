import { useState, useCallback } from "react"

export function useClipboard(resetAfter = 2000) {
  const [copied, setCopied] = useState(false)

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), resetAfter)
    })
  }, [resetAfter])

  return { copied, copy }
}
