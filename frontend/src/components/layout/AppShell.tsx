"use client";

import React, { useState, Suspense } from "react";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";

interface AppShellProps {
  children: React.ReactNode;
}

function SidebarSkeleton() {
  return (
    <aside className="h-full w-64 bg-white border-r border-[#EDEFF3] flex flex-col justify-between" />
  );
}

export function AppShell({ children }: AppShellProps) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[#F5F6F9] text-[#1B1A1A] antialiased">
      {/* Desktop Persistent Sidebar */}
      <div className="hidden md:flex fixed left-0 top-0 h-full w-64 z-50">
        <Suspense fallback={<SidebarSkeleton />}>
          <Sidebar />
        </Suspense>
      </div>

      {/* Mobile Drawer Backdrop & Sidebar */}
      {mobileSidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden flex">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity"
            onClick={() => setMobileSidebarOpen(false)}
            aria-hidden="true"
          />
          {/* Sidebar Panel */}
          <div className="relative z-50 w-64 max-w-[80vw] h-full shadow-xl">
            <Suspense fallback={<SidebarSkeleton />}>
              <Sidebar onCloseMobile={() => setMobileSidebarOpen(false)} />
            </Suspense>
          </div>
        </div>
      )}

      {/* Top Header */}
      <Header onToggleMobileSidebar={() => setMobileSidebarOpen((prev) => !prev)} />

      {/* Main Page Area */}
      <div className="md:pl-64 flex flex-col min-h-screen max-w-full overflow-x-hidden">
        <main className="relative w-full pt-14 min-h-screen bg-[#F5F6F9] min-w-0 max-w-full">
          <Suspense fallback={<div className="p-6 text-[13px] text-[#606366]">Loading...</div>}>
            {children}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
