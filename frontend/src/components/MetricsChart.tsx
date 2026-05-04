'use client';

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface MetricsChartProps {
  data: any; // Metrics data from backend
}

export default function MetricsChart({ data }: MetricsChartProps) {
  // Extract metrics data from backend response
  const results = data.results || [];
  
  if (!results || results.length === 0) {
    return null;
  }

  // Parse metrics data into chart format
  const chartData: any[] = [];
  const metrics: string[] = [];
  let aggregationType = data.query_spec?.aggregation || 'Average'; // Get from backend or default
  let detectedAggregation = '';

  results.forEach((metric: any) => {
    const metricName = metric.name;
    const metricUnit = metric.unit || '';
    metrics.push(metricName);

    const timeseries = metric.timeseries || [];
    timeseries.forEach((series: any) => {
      const dataPoints = series.data || [];
      
      dataPoints.forEach((point: any) => {
        const date = new Date(point.timestamp);
        
        // Format timestamp based on time range to avoid collision
        // For multi-day ranges, include date; for intra-day, show time only
        const totalPoints = dataPoints.length;
        let timestamp: string;
        
        if (totalPoints > 48) {
          // Multi-day range (more than 2 days worth of hourly data)
          // Show: "Jan 22, 14:00" or "22 Jan 14:00"
          timestamp = date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
          });
        } else {
          // Intra-day range (≤ 2 days)
          // Show: "14:00"
          timestamp = date.toLocaleTimeString('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false
          });
        }

        // Find existing data point or create new one
        // Use full ISO timestamp as key to avoid collisions
        const uniqueKey = point.timestamp;
        let existingPoint = chartData.find(d => d.time === uniqueKey);
        if (!existingPoint) {
          existingPoint = { timestamp, time: uniqueKey };
          chartData.push(existingPoint);
        }

        // Add metric value (use average, total, max, etc based on what's available)
        // Also track which aggregation type we're actually using
        if (point.average !== undefined && point.average !== null) {
          existingPoint[metricName] = Number(point.average.toFixed(2));
          detectedAggregation = 'Average';
        } else if (point.total !== undefined && point.total !== null) {
          existingPoint[metricName] = Number(point.total.toFixed(2));
          detectedAggregation = 'Total';
        } else if (point.maximum !== undefined && point.maximum !== null) {
          existingPoint[metricName] = Number(point.maximum.toFixed(2));
          detectedAggregation = 'Maximum';
        } else if (point.minimum !== undefined && point.minimum !== null) {
          existingPoint[metricName] = Number(point.minimum.toFixed(2));
          detectedAggregation = 'Minimum';
        }
      });
    });
  });

  // Use detected aggregation if available, otherwise use from query_spec
  const finalAggregation = detectedAggregation || aggregationType;

  // Sort by timestamp
  chartData.sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

  if (chartData.length === 0) {
    return null;
  }

  // Colors for different metrics
  const colors = ['#0066CC', '#00A3E0', '#FF6B6B', '#4ECDC4', '#95E1D3'];

  // Custom tooltip
  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-white p-3 border border-gray-200 rounded-lg shadow-lg">
          <p className="text-sm font-semibold text-gray-900 mb-2">{label}</p>
          {payload.map((entry: any, index: number) => (
            <p key={index} className="text-sm" style={{ color: entry.color }}>
              <span className="font-medium">{entry.name}:</span>{' '}
              <span className="font-bold">{entry.value}</span>
            </p>
          ))}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="mt-4 p-4 bg-white border border-gray-200 rounded-lg shadow-sm">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <svg className="w-4 h-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
          </svg>
          Metrics Visualization
          <span className="text-xs font-normal text-gray-500 ml-2">({finalAggregation})</span>
        </h3>
      </div>
      
      <ResponsiveContainer width="100%" height={300}>
        <LineChart data={chartData} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
          <XAxis 
            dataKey="timestamp" 
            stroke="#6B7280"
            style={{ fontSize: '12px' }}
            tick={{ fill: '#6B7280' }}
          />
          <YAxis 
            stroke="#6B7280"
            style={{ fontSize: '12px' }}
            tick={{ fill: '#6B7280' }}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend 
            wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }}
            iconType="line"
          />
          {metrics.map((metricName, index) => {
            // Get the unit for this metric
            const metric = results.find((m: any) => m.name === metricName);
            const unit = metric?.unit || '';
            const displayName = unit ? `${metricName} (${unit})` : metricName;
            
            return (
              <Line
                key={metricName}
                type="monotone"
                dataKey={metricName}
                stroke={colors[index % colors.length]}
                strokeWidth={2}
                dot={{ r: 3 }}
                activeDot={{ r: 5 }}
                name={displayName}
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>

      <div className="mt-3 flex items-center justify-between text-xs text-gray-500">
        <span>{chartData.length} data points</span>
        <span className="flex items-center gap-1">
          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
            <path d="M10 2a8 8 0 100 16 8 8 0 000-16zM9 9a1 1 0 012 0v4a1 1 0 01-2 0V9zm1-4a1 1 0 100 2 1 1 0 000-2z" />
          </svg>
          Hover over points for details
        </span>
      </div>
    </div>
  );
}
