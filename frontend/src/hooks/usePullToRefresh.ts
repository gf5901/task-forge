import { useState, useEffect, useRef, useCallback } from "react"

const THRESHOLD = 72   // px of pull needed to trigger
const MAX_PULL = 100   // px max visual stretch

interface Options {
  onRefresh: () => Promise<void>
  scrollRef: React.RefObject<HTMLElement | null>
}

export function usePullToRefresh({ onRefresh, scrollRef }: Options) {
  const [pullY, setPullY] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const startYRef = useRef<number | null>(null)
  const pullingRef = useRef(false)
  const pullYRef = useRef(0)          // always current, readable inside touch handlers
  const refreshingRef = useRef(false)

  const trigger = useCallback(async () => {
    setRefreshing(true)
    refreshingRef.current = true
    setPullY(0)
    pullYRef.current = 0
    try {
      await onRefresh()
    } finally {
      setRefreshing(false)
      refreshingRef.current = false
    }
  }, [onRefresh])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    function onTouchStart(e: TouchEvent) {
      if (el!.scrollTop === 0) {
        startYRef.current = e.touches[0].clientY
        pullingRef.current = false
      }
    }

    function onTouchMove(e: TouchEvent) {
      if (startYRef.current === null || refreshingRef.current) return
      const dy = e.touches[0].clientY - startYRef.current
      if (dy <= 0) { startYRef.current = null; return }
      if (el!.scrollTop > 0) { startYRef.current = null; return }
      pullingRef.current = true
      const visual = Math.min(MAX_PULL, dy * (MAX_PULL / (MAX_PULL + dy)) * 1.6)
      pullYRef.current = visual
      setPullY(visual)
      if (dy > 10) e.preventDefault()
    }

    function onTouchEnd() {
      if (!pullingRef.current) return
      if (pullYRef.current >= THRESHOLD) {
        trigger()
      } else {
        setPullY(0)
        pullYRef.current = 0
      }
      startYRef.current = null
      pullingRef.current = false
    }

    el.addEventListener("touchstart", onTouchStart, { passive: true })
    el.addEventListener("touchmove", onTouchMove, { passive: false })
    el.addEventListener("touchend", onTouchEnd, { passive: true })

    return () => {
      el.removeEventListener("touchstart", onTouchStart)
      el.removeEventListener("touchmove", onTouchMove)
      el.removeEventListener("touchend", onTouchEnd)
    }
  }, [scrollRef, trigger])  // no longer depends on pullY or refreshing — refs handle that

  return { pullY, refreshing }
}
