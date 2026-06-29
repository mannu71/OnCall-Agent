import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import {
  Construction,
  TrendingUp,
  Calendar,
  CheckCircle,
  XCircle
} from 'lucide-react';

// Use Vite's environment check for development mode
const DEV_MODE = import.meta.env.DEV;

function Analytics() {
  // Show under development message if not in dev mode
  if (!DEV_MODE) {
    return (
      <div className="flex flex-col items-center justify-center h-full p-8 text-center">
        <Construction className="w-20 h-20 text-yellow-600 mb-4" />
        <h1 className="text-4xl font-bold mb-2">Analytics</h1>
        <h2 className="text-2xl text-muted-foreground mb-4">🚧 Under Development 🚧</h2>
        <Card className="mt-4 max-w-lg bg-yellow-50 border-yellow-600">
          <CardContent className="p-6">
            <p className="text-base text-foreground mb-4">
              This feature is currently being developed. It will provide insights and analytics about your workflow runs and agent performance.
            </p>
            <p className="text-sm text-muted-foreground">
              In the meantime, you can view workflow run history on the <strong>Dashboard</strong> page.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  // Development mode - show analytics placeholder
  return (
    <div className="p-6">
      <div className="mb-8">
        <h1 className="text-4xl font-bold mb-2">Analytics</h1>
        <p className="text-muted-foreground">Workflow and agent performance metrics (Development Preview)</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Summary Cards */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-2">
              <Calendar className="w-5 h-5 text-primary" />
              <span className="text-sm text-muted-foreground">Total Runs</span>
            </div>
            <h4 className="text-4xl font-bold">--</h4>
          </CardContent>
        </Card>
        
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle className="w-5 h-5 text-green-600" />
              <span className="text-sm text-muted-foreground">Success Rate</span>
            </div>
            <h4 className="text-4xl font-bold">--%</h4>
          </CardContent>
        </Card>
        
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-2">
              <XCircle className="w-5 h-5 text-red-600" />
              <span className="text-sm text-muted-foreground">Failed Runs</span>
            </div>
            <h4 className="text-4xl font-bold">--</h4>
          </CardContent>
        </Card>
        
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 mb-2">
              <TrendingUp className="w-5 h-5 text-blue-600" />
              <span className="text-sm text-muted-foreground">Avg Duration</span>
            </div>
            <h4 className="text-4xl font-bold">--s</h4>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
        {/* Placeholder for charts */}
        <Card className="lg:col-span-2">
          <CardContent className="h-[300px] flex items-center justify-center">
            <div className="text-center">
              <TrendingUp className="w-12 h-12 text-muted-foreground mx-auto mb-2" />
              <p className="text-muted-foreground">Workflow runs chart coming soon</p>
            </div>
          </CardContent>
        </Card>
        
        <Card>
          <CardContent className="h-[300px] flex items-center justify-center">
            <div className="text-center">
              <CheckCircle className="w-12 h-12 text-muted-foreground mx-auto mb-2" />
              <p className="text-muted-foreground">Success/failure breakdown coming soon</p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default Analytics;
