import * as React from "react"
import { useNavigate, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Workflow,
  Calendar,
  Settings,
  BarChart3,
  MessageSquare,
  Eye,
  ChevronLeft
} from 'lucide-react'

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"
import { version } from '../../../package.json'

// Check if we're in development mode
const isDevelopment = import.meta.env.DEV

const navigationItems = [
  {
    id: 'dashboard',
    title: 'Dashboard',
    path: '/',
    icon: LayoutDashboard,
  },
  {
    id: 'oncall-schedule',
    title: 'Scheduler',
    path: '/scheduler',
    icon: Calendar,
  },
  {
    id: 'log-watch',
    title: 'Log Watch',
    path: '/log-watch',
    icon: Eye,
  },
  {
    id: 'chat',
    title: 'Agent Chat',
    path: '/chat',
    icon: MessageSquare,
    devOnly: true,
  },
  {
    id: 'workflow',
    title: 'Workflow',
    path: '/workflow',
    icon: Workflow,
  },
  {
    id: 'analytics',
    title: 'Analytics',
    path: '/analytics',
    icon: BarChart3,
    devOnly: true,
  },
  {
    id: 'settings',
    title: 'Settings',
    path: '/settings',
    icon: Settings,
  },
].filter(item => !item.devOnly || isDevelopment)

export function AppSidebar({ ...props }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { toggleSidebar, state } = useSidebar()

  const isActive = (path) => {
    return location.pathname === path
  }

  return (
    <Sidebar collapsible="icon" className="border-r-0 bg-white" {...props}>
      {/* Header */}
      <SidebarHeader className="h-14 flex items-center px-4 border-b-0 group-data-[collapsible=icon]:h-12 group-data-[collapsible=icon]:px-2 group-data-[collapsible=icon]:justify-center">
        <SidebarMenu className="w-full group-data-[collapsible=icon]:w-auto">
          <SidebarMenuItem>
            <SidebarMenuButton
              size="sm"
              className="w-full h-10 px-2 justify-between hover:bg-slate-50 group-data-[collapsible=icon]:!w-10 group-data-[collapsible=icon]:!h-10 group-data-[collapsible=icon]:!p-2 group-data-[collapsible=icon]:!min-w-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:items-center"
              onClick={toggleSidebar}
            >
              <div className="flex items-center gap-3 flex-1 min-w-0 group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:flex-none group-data-[collapsible=icon]:w-auto">
                <div className="w-7 h-7 flex items-center justify-center flex-shrink-0 group-data-[collapsible=icon]:w-6 group-data-[collapsible=icon]:h-6">
                  <img
                    src="./favicon.ico"
                    alt="OnCall Agent Logo"
                    className="w-full h-full object-contain"
                    style={{ filter: 'none' }}
                  />
                </div>
                <span className="font-semibold text-base text-slate-900 group-data-[collapsible=icon]:hidden">
                  OnCall Agent
                </span>
              </div>
              <ChevronLeft
                className="size-4 text-slate-400 transition-transform duration-200 flex-shrink-0 group-data-[collapsible=icon]:hidden"
              />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      {/* Main Navigation */}
      <SidebarContent className="px-2 py-1 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:py-0 group-data-[collapsible=icon]:flex group-data-[collapsible=icon]:items-center group-data-[collapsible=icon]:mt-1">
        <SidebarGroup className="px-0 group-data-[collapsible=icon]:w-full group-data-[collapsible=icon]:flex group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:items-center">
          <SidebarGroupContent className="group-data-[collapsible=icon]:flex group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:items-center">
            <SidebarMenu className="space-y-0.5 group-data-[collapsible=icon]:space-y-1 group-data-[collapsible=icon]:flex group-data-[collapsible=icon]:flex-col group-data-[collapsible=icon]:items-center">
              {navigationItems.map((item) => {
                const Icon = item.icon
                const active = isActive(item.path)

                return (
                  <SidebarMenuItem key={item.id} className="group-data-[collapsible=icon]:w-auto">
                    <SidebarMenuButton
                      onClick={() => navigate(item.path)}
                      isActive={active}
                      tooltip={item.title}
                      className={cn(
                        "h-11 px-3 rounded-lg transition-all duration-200 font-normal relative !w-full",
                        "group-data-[collapsible=icon]:!h-11 group-data-[collapsible=icon]:!w-11 group-data-[collapsible=icon]:!p-0 group-data-[collapsible=icon]:!justify-center group-data-[collapsible=icon]:!items-center group-data-[collapsible=icon]:!flex",
                        active
                          ? "bg-slate-50 text-slate-900 font-medium shadow-sm"
                          : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                      )}
                    >
                      <Icon className={cn(
                        "w-5 h-5 flex-shrink-0 transition-colors mr-3 group-data-[collapsible=icon]:mr-0",
                        active ? "text-slate-700" : "text-slate-400"
                      )} />
                      <span className="flex-1 text-sm group-data-[collapsible=icon]:sr-only">
                        {item.title}
                      </span>
                      {active && (
                        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-6 bg-red-600 rounded-r-full group-data-[collapsible=icon]:hidden" />
                      )}
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      {/* Footer */}
      <SidebarFooter className="p-4 border-t-0 group-data-[collapsible=icon]:p-2">
        <div className="text-xs text-slate-400 group-data-[collapsible=icon]:hidden">
          v{version}
        </div>
      </SidebarFooter>
    </Sidebar>
  )
}
