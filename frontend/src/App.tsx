import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { AdminPage } from "./pages/AdminPage";
import { CourseDetailPage } from "./pages/CourseDetailPage";
import { CoursesPage } from "./pages/CoursesPage";
import { RegisterPage } from "./pages/RegisterPage";
import { LoginPage } from "./pages/LoginPage";
import { LtiLaunchPage } from "./pages/LtiLaunchPage";
import { LearnerChatPage } from "./pages/LearnerChatPage";
import { LearnFlashcardsPage } from "./pages/LearnFlashcardsPage";

const StudiesPage = lazy(() => import("./pages/StudiesPage").then(m => ({ default: m.StudiesPage })));
const BlindRatingPage = lazy(() => import("./pages/StudiesPage").then(m => ({ default: m.BlindRatingPage })));

// Loaded only when a module is opened: its Markdown renderer is about a fifth of the bundle,
// and no other page needs it before the first screen can be shown.
const ModulePreviewPage = lazy(() =>
  import("./pages/ModulePreviewPage").then((m) => ({ default: m.ModulePreviewPage })),
);

const FlashcardEditorPage = lazy(() =>
  import("./pages/FlashcardEditorPage").then((m) => ({ default: m.FlashcardEditorPage })),
);

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/lti/launch" element={<LtiLaunchPage />} />
        <Route element={<ProtectedRoute adminOnly />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route element={<ProtectedRoute />}>
          <Route path="/learn" element={<LearnerChatPage />} />
          <Route path="/learn/:courseId" element={<LearnerChatPage />} />
          <Route path="/learn/:courseId/flashcards/:setId" element={<LearnFlashcardsPage />} />
        </Route>
        <Route element={<ProtectedRoute authorOnly />}>
          <Route path="/studies" element={<Suspense fallback={null}><StudiesPage /></Suspense>} />
          <Route path="/studies/:studyId" element={<Suspense fallback={null}><BlindRatingPage /></Suspense>} />
          <Route path="/studies/:studyId/items/:itemId" element={<Suspense fallback={null}><BlindRatingPage /></Suspense>} />
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
          <Route
            path="/flashcard-sets/:id"
            element={
              <Suspense fallback={null}>
                <FlashcardEditorPage />
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
