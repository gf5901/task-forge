import { useState } from "react"
import { Bot, LogIn } from "lucide-react"
import { login } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export default function Login() {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError("")
    try {
      await login(email, password)
      window.location.reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 px-4">
      <div className="w-full max-w-xs space-y-6">
        <div className="text-center space-y-3">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600/15">
            <Bot className="size-5 text-indigo-400" />
          </div>
          <h1 className="text-lg font-semibold text-zinc-100">Sign in to Task Forge</h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {error && (
            <div className="rounded-md bg-red-500/10 border border-red-500/20 px-3 py-2 text-[13px] text-red-400">
              {error}
            </div>
          )}
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            required
            className="h-9 bg-zinc-900/50 border-zinc-700/60 text-zinc-200 placeholder:text-zinc-600"
          />
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            required
            className="h-9 bg-zinc-900/50 border-zinc-700/60 text-zinc-200 placeholder:text-zinc-600"
          />
          <Button type="submit" disabled={submitting} className="w-full h-9 bg-indigo-600 hover:bg-indigo-500 text-white text-sm">
            <LogIn className="size-3.5 mr-1.5" />
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </div>
  )
}
