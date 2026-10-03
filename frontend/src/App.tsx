import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AdminPage } from "./pages/AdminPage";
import { CourseDetailPage } from "./pages/CourseDetailPage";
import { CoursesPage } from "./pages/CoursesPage";
import { LoginPage } from "./pages/LoginPage";

// Loaded only when a module is opened: its Markdown renderer is about a fifth of the bundle,
// and no other page needs it before the first screen can be shown.
const ModulePreviewPage = lazy(() =>
  import("./pages/ModulePreviewPage").then((m) => ({ default: m.ModulePreviewPage })),
);

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute role="admin" />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route element={<ProtectedRoute role="teacher" />}>
          <Route path="/courses" element={<CoursesPage />} />
          <Route path="/courses/:courseId" element={<CourseDetailPage />} />
          <Route
            path="/modules/:moduleId"
            element={
              <Suspense fallback={null}>
                <ModulePreviewPage />
              </Suspense>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/courses" replace />} />
      </Routes>
    </AuthProvider>
  );
}

export default App;
