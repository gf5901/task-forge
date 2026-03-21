import { useState, useEffect } from "react"
import { fetchRoles } from "@/lib/api"

interface Role {
  id: string
  label: string
  prompt: string
}

const cache: Role[] = []

export function useRoles() {
  const [roles, setRoles] = useState<Role[]>(cache)

  useEffect(() => {
    if (cache.length > 0) return
    fetchRoles()
      .then(({ roles: fetched }) => {
        cache.push(...fetched)
        setRoles([...cache])
      })
      .catch(() => {})
  }, [])

  function roleLabel(id: string): string {
    if (!id) return ""
    const found = roles.find((r) => r.id === id)
    return found ? found.label : id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  }

  return { roles, roleLabel }
}
