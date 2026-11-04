import { HashRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/shell/AppShell'
import { LibraryPage } from '@/pages/LibraryPage'
import { CodePage } from '@/pages/CodePage'
import { PaperProfilePage } from '@/pages/PaperProfilePage'
import { StatsPage } from '@/pages/StatsPage'
import { ReaderPage } from '@/pages/ReaderPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { TasksPage } from '@/pages/TasksPage'
import { AssetPage } from '@/features/materials/AssetPage'
import { ComparePage } from '@/features/compare/ComparePage'
import { DiscoverHub, LegacyRedirect } from '@/features/discovery/DiscoverHub'
import { VenueDirectoryPage } from '@/features/discovery/VenueDirectoryPage'
import { VenuePage } from '@/features/discovery/VenuePage'
import { GlobalNotesPage } from '@/features/knowledge/GlobalNotesPage'
import { KnowledgePage } from '@/features/knowledge/KnowledgePage'
import { MindMapPage } from '@/features/mindmap/MindMapPage'

export function App() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/library" replace />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="paper/:paperId" element={<PaperProfilePage />} />
          <Route path="reader/:paperId" element={<ReaderPage />} />
          <Route path="code/:paperId" element={<CodePage />} />
          <Route path="mindmap/:paperId" element={<MindMapPage />} />
          <Route path="materials/:assetId" element={<AssetPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="settings/:section" element={<SettingsPage />} />
          <Route path="discover" element={<DiscoverHub />} />
          <Route path="venues" element={<VenueDirectoryPage />} />
          <Route path="venue" element={<VenuePage />} />
          <Route path="recommend" element={<LegacyRedirect tab="recommend" />} />
          <Route path="random" element={<LegacyRedirect tab="random" />} />
          <Route path="compare" element={<ComparePage />} />
          <Route path="knowledge" element={<KnowledgePage />} />
          <Route path="notes" element={<GlobalNotesPage />} />
          <Route path="*" element={<Navigate to="/library" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  )
}
