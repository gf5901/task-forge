import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { useEffect, useState } from "react"
import { Toaster } from "react-hot-toast"
import { TooltipProvider } from "@/components/ui/tooltip"
import { checkAuth } from "@/lib/api"
import Layout from "@/components/Layout"
import TaskList from "@/pages/TaskList"
import TaskDetail from "@/pages/TaskDetail"
import TaskCreate from "@/pages/TaskCreate"
import Login from "@/pages/Login"
import ActivityLog from "@/pages/ActivityLog"
import Spec from "@/pages/Spec"
import ProjectList from "@/pages/ProjectList"
import ProjectCreate from "@/pages/ProjectCreate"
import ProjectDetail from "@/pages/ProjectDetail"
import SettingsPage from "@/pages/Settings"
import Stats from "@/pages/Stats"

function App() {
  const [authState, setAuthState] = useState<{
    checked: boolean
    authenticated: boolean
    authEnabled: boolean
  }>({ checked: false, authenticated: false, authEnabled: false })

  useEffect(() => {
    checkAuth()
      .then((data) =>
        setAuthState({
          checked: true,
          authenticated: data.authenticated,
          authEnabled: data.auth_enabled,
        }),
      )
      .catch(() =>
        setAuthState({ checked: true, authenticated: false, authEnabled: true }),
      )
  }, [])

  if (!authState.checked) {
    return (
      <div className="flex h-screen items-center justify-center bg-zinc-950">
        <div className="animate-pulse text-zinc-500 text-sm">Loading…</div>
      </div>
    )
  }

  if (authState.authEnabled && !authState.authenticated) {
    return (
      <BrowserRouter>
        <Login />
      </BrowserRouter>
    )
  }

  return (
    <BrowserRouter>
      <TooltipProvider>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: "#18181b",
              color: "#e4e4e7",
              border: "1px solid #3f3f46",
              borderRadius: "8px",
              fontSize: "13px",
            },
            success: { iconTheme: { primary: "#10b981", secondary: "#18181b" } },
            error: { iconTheme: { primary: "#f87171", secondary: "#18181b" } },
          }}
        />
        <Routes>
          <Route element={<Layout />}>
            <Route path="/tasks" element={<TaskList />} />
            <Route path="/tasks/new" element={<TaskCreate />} />
            <Route path="/tasks/:taskId" element={<TaskDetail />} />
            <Route path="/activity" element={<ActivityLog />} />
            <Route path="/stats" element={<Stats />} />
            <Route path="/spec" element={<Spec />} />
            <Route path="/projects" element={<ProjectList />} />
            <Route path="/projects/new" element={<ProjectCreate />} />
            <Route path="/projects/:projectId" element={<ProjectDetail />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/tasks" replace />} />
        </Routes>
      </TooltipProvider>
    </BrowserRouter>
  )
}

export default App
