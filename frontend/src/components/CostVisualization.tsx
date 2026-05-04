'use client';

import React from 'react';
import { ArrowTrendingUpIcon } from '@heroicons/react/24/outline';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

interface DailyTrendItem {
  date: string;
  cost: number;
}

interface CostBreakdownItem {
  servicename?: string;
  subscriptionname?: string;
  location?: string;
  resourcelocation?: string;
  resourcegroupname?: string;
  resourceid?: string;
  resourcename?: string;
  cost: number;
}

interface CostVisualizationProps {
  data: {
    total_cost?: number;
    currency?: string;
    daily_trend?: DailyTrendItem[];
    breakdown?: CostBreakdownItem[];
    by_subscription?: Record<string, any>;
  };
  showBreakdown?: boolean; // Optional: if false, only shows the trend graph
}

const CostVisualization: React.FC<CostVisualizationProps> = ({ data, showBreakdown = true }) => {
  const { daily_trend, breakdown, total_cost, currency = 'SEK', by_subscription } = data;

  const formatCurrency = (value: number, curr: string) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: curr,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  };

  // Determine what to display as the breakdown label
  const getBreakdownLabel = (item: CostBreakdownItem): string => {
    if (item.subscriptionname) return item.subscriptionname;
    if (item.servicename) return item.servicename;
    if (item.resourcename) return item.resourcename;
    if (item.resourceid) return item.resourceid.split('/').pop() || item.resourceid;
    if (item.resourcegroupname) return item.resourcegroupname;
    if (item.location) return item.location;
    if (item.resourcelocation) return item.resourcelocation;
    return 'Unknown';
  };

  // Determine the breakdown type for the header
  const getBreakdownType = (): string => {
    if (!breakdown || breakdown.length === 0) return 'Items';
    const firstItem = breakdown[0];
    if (firstItem.subscriptionname) return 'Subscriptions';
    if (firstItem.resourcegroupname) return 'Resource Groups';
    if (firstItem.resourceid) return 'Resources';
    if (firstItem.location || firstItem.resourcelocation) return 'Locations';
    if ((firstItem as any).resourcetype) return 'Resource Types';
    if (firstItem.servicename) return 'Services';
    return 'Items';
  };

  return (
    <div className="space-y-6">
      {/* Daily Trend Chart */}
      {daily_trend && daily_trend.length > 0 && (
        <div className="bg-white rounded-lg shadow-md p-3 sm:p-6 border border-gray-200">
          <h3 className="text-sm sm:text-lg font-semibold text-gray-900 mb-3 sm:mb-4 flex items-center gap-2">
            <ArrowTrendingUpIcon className="h-4 w-4 sm:h-5 sm:w-5 text-indigo-500" />
            Cost Trends Over Time
          </h3>
          <div className="h-48 sm:h-64">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={daily_trend}>
                <defs>
                  <linearGradient id="colorCostTrend" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8}/>
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis 
                  dataKey="date" 
                  stroke="#6b7280"
                  tick={{ fontSize: 12 }}
                  tickFormatter={(value) => {
                    if (!value) return '';
                    const str = value.toString();
                    if (str.length === 8) {
                      const day = str.substring(6, 8);
                      const monthIndex = parseInt(str.substring(4, 6)) - 1;
                      const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
                      return `${day} ${monthNames[monthIndex]}`;
                    }
                    return value;
                  }}
                />
                <YAxis 
                  stroke="#6b7280"
                  tick={{ fontSize: 12 }}
                  tickFormatter={(value) => `${value.toFixed(0)}`}
                />
                <Tooltip 
                  contentStyle={{ backgroundColor: '#ffffff', border: '1px solid #e5e7eb', borderRadius: '0.5rem' }}
                  labelFormatter={(value) => {
                    if (!value) return '';
                    const str = value.toString();
                    if (str.length === 8) {
                      const year = str.substring(0, 4);
                      const month = str.substring(4, 6);
                      const day = str.substring(6, 8);
                      return `${year}-${month}-${day}`;
                    }
                    return value;
                  }}
                  formatter={(value: any) => [`${parseFloat(value).toFixed(2)} ${currency}`, 'Cost']}
                />
                <Area type="monotone" dataKey="cost" stroke="#3b82f6" fillOpacity={1} fill="url(#colorCostTrend)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 sm:mt-4 flex flex-col sm:flex-row items-center justify-center gap-2 sm:gap-4 text-xs sm:text-sm text-gray-600">
            <span>Total: {formatCurrency(daily_trend.reduce((sum, item) => sum + item.cost, 0), currency)}</span>
            <span className="hidden sm:inline">•</span>
            <span>Average Daily: {formatCurrency((daily_trend.reduce((sum, item) => sum + item.cost, 0) / daily_trend.length), currency)}</span>
          </div>
        </div>
      )}

      {/* Cost Breakdown Table */}
      {showBreakdown && breakdown && breakdown.length > 0 && (
        <div className="bg-white rounded-lg shadow-md border border-gray-200 overflow-hidden">
          <div className="px-3 sm:px-6 py-3 sm:py-4 border-b border-gray-200">
            <h3 className="text-sm sm:text-lg font-semibold text-gray-900">
              Cost Breakdown by {getBreakdownType()}
            </h3>
            {total_cost !== undefined && (
              <p className="text-xs sm:text-sm text-gray-600 mt-1">
                Total: {formatCurrency(total_cost, currency)}
              </p>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-2 sm:px-6 py-2 sm:py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    {getBreakdownType().slice(0, -1)}
                  </th>
                  <th scope="col" className="px-2 sm:px-6 py-2 sm:py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Cost
                  </th>
                  <th scope="col" className="px-2 sm:px-6 py-2 sm:py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    % of Total
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {breakdown
                  .sort((a, b) => b.cost - a.cost)
                  .map((item, index) => {
                    const percentage = total_cost ? (item.cost / total_cost) * 100 : 0;
                    return (
                      <tr key={index} className="hover:bg-gray-50 transition-colors">
                        <td className="px-2 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm font-medium text-gray-900 break-words max-w-[150px] sm:max-w-none">
                          {getBreakdownLabel(item)}
                        </td>
                        <td className="px-2 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm text-gray-900 text-right font-mono">
                          {formatCurrency(item.cost, currency)}
                        </td>
                        <td className="px-2 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm text-gray-500 text-right">
                          <div className="flex items-center justify-end gap-1 sm:gap-2">
                            <div className="hidden xs:block w-12 sm:w-16 h-2 bg-gray-200 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-indigo-500 rounded-full" 
                                style={{ width: `${Math.min(percentage, 100)}%` }}
                              />
                            </div>
                            <span className="w-10 sm:w-12 text-right">{percentage.toFixed(1)}%</span>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* By Subscription Breakdown (if available) */}
      {by_subscription && Object.keys(by_subscription).length > 0 && (
        <div className="bg-white rounded-lg shadow-md border border-gray-200 overflow-hidden">
          <div className="px-3 sm:px-6 py-3 sm:py-4 border-b border-gray-200">
            <h3 className="text-sm sm:text-lg font-semibold text-gray-900">
              Cost by Subscription
            </h3>
          </div>
          <div className="overflow-x-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
            <table className="w-full sm:min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th scope="col" className="px-2 sm:px-6 py-2 sm:py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Subscription ID
                  </th>
                  <th scope="col" className="px-2 sm:px-6 py-2 sm:py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Cost
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {Object.entries(by_subscription)
                  .sort(([, a]: any, [, b]: any) => b - a)
                  .map(([subId, cost]: [string, any], index) => (
                    <tr key={index} className="hover:bg-gray-50 transition-colors">
                      <td className="px-2 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm font-medium text-gray-900 break-words max-w-[200px] sm:max-w-none">
                        {subId}
                      </td>
                      <td className="px-2 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm text-gray-900 text-right font-mono">
                        {formatCurrency(typeof cost === 'number' ? cost : 0, currency)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* No Data Message */}
      {(!daily_trend || daily_trend.length === 0) && (!breakdown || breakdown.length === 0) && (
        <div className="text-center py-16 bg-white rounded-lg border-2 border-dashed border-gray-300">
          <svg className="mx-auto h-16 w-16 text-gray-400 mb-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-base font-medium text-gray-900 mb-1">No cost data available</p>
          <p className="text-sm text-gray-500">Try adjusting your query or time range</p>
        </div>
      )}
    </div>
  );
};

export default CostVisualization;
