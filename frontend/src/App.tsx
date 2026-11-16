import { lazy, Suspense } from "react";
import { createBrowserRouter, createRoutesFromElements, Navigate, Route, RouterProvider } from "react-router-dom";
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
import { LearnVideoPage } from "./pages/LearnVideoPage";

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

const VideoEditorPage = lazy(() =>
  import("./pages/VideoEditorPage").then((m) => ({ default: m.VideoEditorPage })),
);

// A data router, so that an editor can ask before its unsaved edits are left (useBlocker).
const router = createBrowserRouter(createRoutesFromElements(
  <>
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
      <Route path="/learn/:courseId/videos/:videoId" element={<LearnVideoPage />} />
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
      <Route
        path="/videos/:id"
        element={
          <Suspense fallback={null}>
            <VideoEditorPage />
          </Suspense>
        }
      />
    </Route>
    <Route path="*" element={<Navigate to="/courses" replace />} />
  </>,
));

// AuthProvider wraps the router: the routes need the session, and nothing in it needs the router.
function App() {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}

export default App;
