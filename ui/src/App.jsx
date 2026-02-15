import React, { lazy, Suspense } from 'react';
import { ThemeProvider, createTheme, CssBaseline, Box, Typography, Container, CircularProgress } from '@mui/material';
import { BrowserRouter, HashRouter, Routes, Route } from 'react-router-dom';
import { SidebarProvider, useSidebar } from './context/SidebarContext';
import { SchedulerProvider } from './context/SchedulerContext';
import { WorkflowStatusProvider } from './context/WorkflowStatusContext';
import Sidebar from './components/sidebar/Sidebar';
import Dashboard from './pages/Dashboard';
import Scheduler from './pages/Scheduler';
import Settings from './pages/Settings';
import './App.css';

// Lazy load heavy components
const Workflow = lazy(() => import('./pages/workflow'));
const Analytics = lazy(() => import('./pages/Analytics'));
const Chat = lazy(() => import('./pages/Chat'));

// Use HashRouter for Electron, BrowserRouter for web
const Router = window.electronAPI ? HashRouter : BrowserRouter;

// Check if we're in development mode
const isDevelopment = import.meta.env.DEV;

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#1976d2',
    },
    secondary: {
      main: '#dc004e',
    },
    background: {
      default: '#ffffff',
    },
  },
  typography: {
    h4: {
      fontWeight: 600,
    },
    h5: {
      fontWeight: 500,
    },
  },
});


// Loading component for lazy-loaded routes
const LoadingFallback = () => (
  <Box
    sx={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      backgroundColor: 'background.default',
    }}
  >
    <CircularProgress />
  </Box>
);

const PlaceholderPage = ({ title }) => {
  return (
    <Box
      sx={{
        p: 4,
        minHeight: '100vh',
        backgroundColor: 'background.default',
      }}
    >
      <Container maxWidth="lg" sx={{ px: 0 }}>
        <Typography variant="h4" gutterBottom sx={{ fontWeight: 600, mb: 3 }}>
          {title}
        </Typography>
        <Typography variant="body1" color="text.secondary">
          This page is under development.
        </Typography>
      </Container>
    </Box>
  );
};

// Main app routes component that has access to sidebar context
const AppRoutes = () => {
  const { isOpen } = useSidebar();
  return (
    <Box sx={{ minHeight: '100vh' }}>
      <Sidebar />
      <Box
        component="main"
        sx={{
          minHeight: '100vh',
          ml: {
            md: isOpen ? '270px' : '64px',
            xs: 0
          },
          transition: 'margin-left 225ms cubic-bezier(0.4, 0, 0.6, 1)',
          overflowX: 'hidden',
        }}
      >
        <Suspense fallback={<LoadingFallback />}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/scheduler" element={<Scheduler />} />
            {isDevelopment && <Route path="/chat" element={<Chat />} />}
            <Route path="/incidents" element={<PlaceholderPage title="Incidents" />} />
            <Route path="/workflow" element={<Workflow />} />
            <Route path="/alerts" element={<PlaceholderPage title="Alerts" />} />
            {isDevelopment && <Route path="/analytics" element={<Analytics />} />}
            <Route path="/emergency" element={<PlaceholderPage title="Emergency Contact" />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </Suspense>
      </Box>
    </Box>
  );
};

export default function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <SidebarProvider>
        <SchedulerProvider>
          <WorkflowStatusProvider>
            <Router>
              <AppRoutes />
            </Router>
          </WorkflowStatusProvider>
        </SchedulerProvider>
      </SidebarProvider>
    </ThemeProvider>
  );
}
