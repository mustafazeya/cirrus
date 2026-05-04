'use client';

import { Fragment, useState, useEffect, useCallback } from 'react';
import { Menu, Transition } from '@headlessui/react';
import {
  HomeIcon,
  SparklesIcon,
  ChartBarIcon,
  CurrencyDollarIcon,
  ShieldCheckIcon,
  Cog6ToothIcon,
  Bars3Icon,
  XMarkIcon,
  ArrowRightStartOnRectangleIcon,
  UserCircleIcon,
  ChevronUpDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline';
import { useMsal } from '@azure/msal-react';
import { useAppStore } from '../store/appStore';
import { usePathname } from 'next/navigation';
import Link from 'next/link';
import UserAvatar from './UserAvatar';
import CirrusAvatar from './CirrusAvatar';

const baseNavItems = [
  { name: 'Home',          href: '/',         icon: HomeIcon },
  { name: 'AI Assistant',  href: '/ai',        icon: SparklesIcon },
  { name: 'Dashboard',     href: '/dashboard', icon: ChartBarIcon },
  { name: 'Cost Analysis', href: '/costs',     icon: CurrencyDollarIcon },
];
const networkNavItems = [{ name: 'Firewall', href: '/firewall', icon: ShieldCheckIcon }];
const adminNavItems   = [{ name: 'Admin',    href: '/admin',    icon: Cog6ToothIcon   }];

interface NavItemDef {
  name: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
}

function NavLink({
  item, active, collapsed, onClick,
}: {
  item: NavItemDef;
  active: boolean;
  collapsed: boolean;
  onClick?: () => void;
}) {
  return (
    <Link
      href={item.href}
      onClick={onClick}
      title={collapsed ? item.name : undefined}
      className={`nav-item touch-target-small ${active ? 'active' : ''}`}
    >
      <item.icon className="nav-icon flex-shrink-0" aria-hidden="true" />
      <span className="nav-label">{item.name}</span>
    </Link>
  );
}

function SidebarContent({
  collapsed,
  onToggleCollapse,
  onClose,
}: {
  collapsed: boolean;
  onToggleCollapse?: () => void;
  onClose?: () => void;
}) {
  const { instance, accounts } = useMsal();
  const { user, isAuthenticated } = useAppStore();
  const pathname = usePathname();
  const [isAdmin,   setIsAdmin]   = useState(false);
  const [isNetwork, setIsNetwork] = useState(false);

  const handleLogout = () => instance.logout();
  const handleLogin  = () => instance.loginRedirect();

  const isActive = (href: string) =>
    href === '/' ? pathname === '/' : pathname.startsWith(href);

  useEffect(() => {
    const roles = (accounts?.[0]?.idTokenClaims as { roles?: string[] })?.roles ?? [];
    setIsAdmin(roles.includes('Admin'));
    setIsNetwork(roles.includes('Network') || roles.includes('Advance') || roles.includes('Admin'));
  }, [user, accounts]);

  const allItems: NavItemDef[] = [
    ...baseNavItems,
    ...(isNetwork ? networkNavItems : []),
    ...(isAdmin   ? adminNavItems   : []),
  ];

  const isDesktop = !onClose;

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Brand header */}
      <div className="flex items-center h-14 border-b border-slate-100 flex-shrink-0 px-3 gap-2">
        <Link
          href="/"
          onClick={onClose}
          title={collapsed ? 'CIRRUS' : undefined}
          className="flex-shrink-0"
        >
          <div className="w-8 h-8 rounded-xl overflow-hidden hover:opacity-90 transition-opacity">
            <CirrusAvatar size="sm" />
          </div>
        </Link>

        <div className="sidebar-brand-text flex-1 min-w-0 overflow-hidden">
          <p className="text-[13px] font-bold text-slate-900 leading-none tracking-tight whitespace-nowrap">CIRRUS</p>
          <p className="text-[10px] text-slate-400 mt-0.5 leading-none whitespace-nowrap">Cloud Intelligence</p>
        </div>

        {/* Desktop collapse/expand toggle */}
        {isDesktop && onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="flex-shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors touch-target-small"
          >
            {collapsed
              ? <ChevronRightIcon className="w-3.5 h-3.5" />
              : <ChevronLeftIcon  className="w-3.5 h-3.5" />}
          </button>
        )}

        {/* Mobile close button */}
        {onClose && (
          <button
            onClick={onClose}
            className="flex-shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors touch-target-small"
          >
            <XMarkIcon className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 overflow-y-auto space-y-0.5">
        {isAuthenticated ? (
          allItems.map(item => (
            <NavLink
              key={item.href}
              item={item}
              active={isActive(item.href)}
              collapsed={collapsed}
              onClick={onClose}
            />
          ))
        ) : (
          <p className="nav-label text-xs text-slate-400 text-center py-6 px-2">
            Sign in to navigate
          </p>
        )}
      </nav>

      {/* User profile */}
      <div className="border-t border-slate-100 p-2 flex-shrink-0">
        {isAuthenticated ? (
          collapsed ? (
            <button
              onClick={onToggleCollapse}
              title={user?.name || user?.email || 'Expand sidebar'}
              className="w-full flex items-center justify-center py-1.5 rounded-lg hover:bg-slate-50 transition-colors"
            >
              <UserAvatar photoUrl={user?.photoUrl} name={user?.name || user?.email} size="sm" />
            </button>
          ) : (
            <Menu as="div" className="relative">
              <Menu.Button className="w-full flex items-center gap-2.5 px-2 py-2 rounded-lg hover:bg-slate-50 transition-colors text-left group">
                <UserAvatar photoUrl={user?.photoUrl} name={user?.name || user?.email} size="sm" />
                <div className="sidebar-user-text flex-1 min-w-0 overflow-hidden">
                  <p className="text-[12.5px] font-semibold text-slate-800 truncate leading-tight">
                    {user?.name || user?.email || 'User'}
                  </p>
                  {user?.name && user?.email && (
                    <p className="text-[10.5px] text-slate-400 truncate leading-tight mt-0.5">
                      {user.email}
                    </p>
                  )}
                </div>
                <ChevronUpDownIcon className="w-3.5 h-3.5 text-slate-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              </Menu.Button>

              <Transition
                as={Fragment}
                enter="transition ease-out duration-100"
                enterFrom="transform opacity-0 scale-95"
                enterTo="transform opacity-100 scale-100"
                leave="transition ease-in duration-75"
                leaveFrom="transform opacity-100 scale-100"
                leaveTo="transform opacity-0 scale-95"
              >
                <Menu.Items className="absolute bottom-full left-0 right-0 mb-1 rounded-xl bg-white shadow-xl ring-1 ring-slate-200 focus:outline-none z-50 py-1 overflow-hidden">
                  <Menu.Item>
                    {({ active }) => (
                      <Link
                        href="/profile"
                        onClick={onClose}
                        className={`flex items-center gap-2.5 px-3 py-2.5 text-[13px] text-slate-700 ${active ? 'bg-slate-50' : ''}`}
                      >
                        <UserCircleIcon className="w-4 h-4 text-slate-400" />
                        Your Profile
                      </Link>
                    )}
                  </Menu.Item>
                  <div className="my-0.5 h-px bg-slate-100 mx-2" />
                  <Menu.Item>
                    {({ active }) => (
                      <button
                        onClick={handleLogout}
                        className={`flex items-center gap-2.5 w-full px-3 py-2.5 text-[13px] text-red-600 ${active ? 'bg-red-50' : ''}`}
                      >
                        <ArrowRightStartOnRectangleIcon className="w-4 h-4" />
                        Sign out
                      </button>
                    )}
                  </Menu.Item>
                </Menu.Items>
              </Transition>
            </Menu>
          )
        ) : (
          <button
            onClick={handleLogin}
            title={collapsed ? 'Sign in' : undefined}
            className="w-full flex items-center justify-center gap-2 px-2 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-[13px] font-semibold transition-colors"
          >
            {collapsed
              ? <ArrowRightStartOnRectangleIcon className="w-4 h-4 flex-shrink-0" />
              : <span>Sign in</span>}
          </button>
        )}
      </div>
    </div>
  );
}

export default function Navbar() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(localStorage.getItem('sidebarCollapsed') === 'true');
  }, []);

  const { user, isAuthenticated } = useAppStore();
  const pathname = usePathname();

  useEffect(() => { setMobileOpen(false); }, [pathname]);

  const toggleCollapse = useCallback(() => {
    setCollapsed(prev => {
      const next = !prev;
      localStorage.setItem('sidebarCollapsed', String(next));
      return next;
    });
  }, []);

  return (
    <>
      {/* Desktop sidebar (lg+) */}
      <aside
        className="app-sidebar hidden lg:flex"
        data-collapsed={collapsed ? 'true' : 'false'}
      >
        <SidebarContent collapsed={collapsed} onToggleCollapse={toggleCollapse} />
      </aside>

      {/* Mobile fixed top bar */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-40 h-14 bg-white border-b border-slate-200 flex items-center px-4 gap-3 shadow-sm">
        <button
          onClick={() => setMobileOpen(true)}
          className="p-2 -ml-1 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors touch-target-small"
          aria-label="Open navigation"
        >
          <Bars3Icon className="w-5 h-5" />
        </button>
        <Link href="/" className="flex items-center gap-2.5 flex-1 min-w-0">
          <div className="w-8 h-8 rounded-lg overflow-hidden flex-shrink-0">
            <CirrusAvatar size="sm" />
          </div>
          <span className="text-sm font-bold text-slate-900 truncate">CIRRUS</span>
        </Link>
        {isAuthenticated && (
          <UserAvatar photoUrl={user?.photoUrl} name={user?.name || user?.email} size="sm" />
        )}
      </div>

      {/* Mobile overlay drawer */}
      {mobileOpen && (
        <>
          <div
            className="lg:hidden fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside
            className="lg:hidden fixed top-0 left-0 bottom-0 z-50 bg-white shadow-2xl animate-slide-in-left"
            style={{ width: '264px' }}
          >
            {/* Mobile drawer is always fully expanded */}
            <SidebarContent collapsed={false} onClose={() => setMobileOpen(false)} />
          </aside>
        </>
      )}
    </>
  );
}
