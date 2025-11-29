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
  },
  {
    id: 'settings',
    title: 'Settings',
    path: '/settings',
    icon: Settings,
  },
];