import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import './index.css'
import App from './App'
import { FleetOverview }     from './screens/FleetOverview'
import { EngineDetail }      from './screens/EngineDetail'
import { ImpactExplorer }    from './screens/ImpactExplorer'
import { PlanningDashboard } from './screens/PlanningDashboard'

const router = createBrowserRouter([
  {
    path: '/',
    element: <App />,
    children: [
      { index: true,                element: <FleetOverview /> },
      { path: 'engines/:id',        element: <EngineDetail /> },
      { path: 'engines/:id/impact', element: <ImpactExplorer /> },
      { path: 'plan',               element: <PlanningDashboard /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
