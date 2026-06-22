import React, { lazy, Suspense } from 'react';
import { BrowserRouter, HashRouter, Routes, Route } from 'react-router-dom';
import { SchedulerProvider } from './context/SchedulerContext';
import { WorkflowStatusProvider } from './context/WorkflowStatusContext';
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Loader2 } from 'lucide-react';
import { AppSidebar } from './components/sidebar/AppSidebar';
import './App.css';

// Lazy load route pages — keeps initial bundle small
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Scheduler = lazy(() => import('./pages/Scheduler'));
const Settings = lazy(() => import('./pages/Settings'));
const Workflow = lazy(() => import('./pages/workflow'));
const Chat = lazy(() => import('./pages/Chat'));
const CodebaseExplorer = lazy(() => import('./pages/CodebaseExplorer'));
const Skills = lazy(() => import('./pages/Skills'));

// Use HashRouter for Electron, BrowserRouter for web
const Router = window.electronAPI ? HashRouter : BrowserRouter;

// Check if we're in development mode
const isDevelopment = import.meta.env.DEV;

// Loading component for lazy-loaded routes
const LoadingFallback = () => (
  <div className="flex justify-center items-center min-h-screen bg-background">
    <Loader2 className="w-8 h-8 animate-spin text-primary" />
  </div>
);

const PlaceholderPage = ({ title }) => {
  return (
    <div className="p-8 min-h-screen bg-background">
      <div className="max-w-7xl mx-auto px-0">
        <h1 className="text-3xl font-semibold mb-6">
          {title}
        </h1>
        <p className="text-base text-muted-foreground">
          This page is under development.
        </p>
      </div>
    </div>
  );
};

// Main app routes component
const AppRoutes = () => {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <div className="flex min-h-0 w-full max-w-full flex-1 flex-col overflow-x-hidden">
          <Suspense fallback={<LoadingFallback />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/scheduler" element={<Scheduler />} />
              <Route path="/chat" element={<Chat />} />
              <Route path="/incidents" element={<PlaceholderPage title="Incidents" />} />
              <Route path="/workflow" element={<Workflow />} />
              <Route path="/explorer" element={<CodebaseExplorer />} />
              <Route path="/skills" element={<Skills />} />
              <Route path="/alerts" element={<PlaceholderPage title="Alerts" />} />
              <Route path="/emergency" element={<PlaceholderPage title="Emergency Contact" />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Dashboard />} />
            </Routes>
          </Suspense>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
};

export default function App() {
  return (
    <TooltipProvider>
      <SchedulerProvider>
        <WorkflowStatusProvider>
          <Router>
            <AppRoutes />
          </Router>
        </WorkflowStatusProvider>
      </SchedulerProvider>
    </TooltipProvider>
  );
}
