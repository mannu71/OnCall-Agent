import { useLocation } from 'react-router-dom';

const WORKFLOW_ROUTES = new Set(['/', '/dashboard', '/scheduler', '/workflow']);

export function useRouteNeedsWorkflows() {
  const { pathname } = useLocation();
  return WORKFLOW_ROUTES.has(pathname);
}

export function useRouteNeedsActiveWorkflows() {
  const { pathname } = useLocation();
  return pathname === '/' || pathname === '/dashboard' || pathname === '/workflow';
}

export function useRouteNeedsExecutions() {
  const { pathname } = useLocation();
  return pathname === '/' || pathname === '/dashboard';
}

export function useRouteNeedsSettings() {
  const { pathname } = useLocation();
  return pathname === '/settings';
}

export function useRouteNeedsChat() {
  const { pathname } = useLocation();
  return pathname === '/chat';
}
