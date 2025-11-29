import {
  Dashboard,
  AccountTree,
  Assignment,
  Schedule,
  Notifications,
  Settings,
  Analytics,
  Phone
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
    id: 'workflow',
    title: 'Workflow',
    path: '/workflow',
    icon: AccountTree,
  },
  {
    id: 'alerts',
    title: 'Alerts',
    path: '/alerts',
    icon: Notifications,
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