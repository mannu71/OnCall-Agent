import {
  Dashboard,
  AccountTree,
  Assignment,
  Schedule,
  Settings,
  Analytics,
  Phone,
  Chat
} from '@mui/icons-material';

// Check if we're in development mode
const isDevelopment = import.meta.env.DEV;

export const navigationItems = [
  {
    id: 'dashboard',
    title: 'Dashboard',
    path: '/',
    icon: Dashboard,
  },
  {
    id: 'oncall-schedule',
    title: 'Scheduler',
    path: '/scheduler',
    icon: Schedule,
  },
  {
    id: 'chat',
    title: 'Agent Chat',
    path: '/chat',
    icon: Chat,
    devOnly: true, // Only show in development mode
  },
  {
    id: 'workflow',
    title: 'Workflow',
    path: '/workflow',
    icon: AccountTree,
  },
  {
    id: 'analytics',
    title: 'Analytics',
    path: '/analytics',
    icon: Analytics,
    devOnly: true, // Only show in development mode
  },
  {
    id: 'settings',
    title: 'Settings',
    path: '/settings',
    icon: Settings,
  },
].filter(item => !item.devOnly || isDevelopment);