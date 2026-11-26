// Route setup and document workflows live in this module.
import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import SettingsPage from "./SettingsPage";
import UsersPage from "./UsersPage";
import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import LoginPage from "./LoginPage";
import FirstRunSetup from "../components/FirstRunSetup";
import DashboardPage from "./DashboardPage";
import ApplicationsPage from "./ApplicationsPage";
import RequirementsPage from "./PremiumRequirementsPage";
import ReportsPage from "./ReportsPage";
import VerificationResultsPage from "./VerificationResultsPage";
import { api, getStoredAuthToken, useSecureData } from "./pageUtils";
import {
  Bell,
  BriefcaseBusiness,
  ChevronDown,
  FileText,
  Globe,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Settings,
  ShieldCheck,
  SunMedium,
  UserRound,
  X,
  Search,
  UploadCloud,
  FolderCog,
} from "lucide-react";

import "../Workspace.css";

const resources = {
  en: {
    translation: {
      appName: "GLOBAL EAGLE",
      totalApplications: "Total Applications",
      approvedApplications: "Approved Applications",
      rejectedApplications: "Rejected Applications",
      completeApplications: "Complete Applications",
      incompleteApplications: "Incomplete Applications",
      pendingVerification: "Pending Verification",
      averageReadiness: "Average Readiness Score",
      recentApplications: "Recent Applications",
      applicant: "Applicant",
      visaType: "Visa Type",
      readiness: "Readiness",
      created: "Created",
      applicationDate: "Application Date",
      viewReport: "View report",
      noApplications: "No applications yet",
      visaTypeDistribution: "Visa Type Distribution",
      readinessTrend: "Readiness Score Trend",
      applicationTrend: "Application Trend",
      email: "Email",
      password: "Password",
      login: "Login",
      register: "Register",
      welcomeBack: "Welcome back",
      signInToContinue: "Sign in to continue to your agency workspace",
      overview: "Overview",
      dashboard: "Dashboard",
      visaApplicationsTitle: "Visa Applications",
      visaDashboard: "Visa dashboard",
      applicationsAndVerification: "Applications and document verification",
      dashboardDescription:
        "Track readiness, review uploaded documents and manage every visa application in one place.",
      newApplication: "New Application",
      loading: "Loading...",
      applicationPipeline:
        "Application pipeline · where each application stands",
      personalInfo: "Personal Info",
      visaAndTravel: "Visa & Travel",
      reviewAndSubmit: "Review & Submit",
      applicantsCount: "{{count}} applicants",
      configuredApplications: "{{count}} applications",
      documentsCount: "{{count}} documents uploaded",
      awaitingReview: "{{count}} awaiting review",
      documentReadinessByApplication: "Document readiness by application",
      searchApplications: "Search applicant or application number",
      filterByStatus: "Filter by status",
      filterByVisaType: "Filter by visa type",
      allVisaTypes: "All visa types",
      applicationsShown: "application(s)",
      latestApplicationsNote: "Latest applications",
      needsAttention: "Needs attention",
      reviewQueue: "Review queue",
      documentsAwaitingReview:
        "{{count}} documents awaiting automated verification",
      openReviewQueue: "Open review queue",
      reuploadRequestedTitle: "Re-upload requested",
      documentsNeedReupload:
        "{{count}} documents need a new file from the applicant",
      lowReadiness: "Low readiness",
      applicationsBelowReadiness:
        "{{count}} applications are below 50% complete",
      readinessDisclaimer:
        "Readiness measures document completeness only. It is not a visa approval prediction.",
      customers: "Customers",
      applications: "Visa Applications",
      documents: "Documents",
      verification: "AI Verification",
      requirements: "Requirements Management",
      reports: "Verification Reports",
      reportTitle: "Verification Reports",
      reportDescription:
        "Monitor document verification activity and review application reports.",
      reportDateRange: "Date range",
      reportLast7Days: "Last 7 days",
      reportLast30Days: "Last 30 days",
      reportLast90Days: "Last 90 days",
      reportLast12Months: "Last 12 months",
      reportExport: "Export CSV",
      reportSummary: "Verification summary",
      reportTotalVerifications: "Total documents",
      reportVerified: "Verified",
      reportRejected: "Rejected",
      reportPending: "Pending",
      reportNoPreviousData: "No previous-period data",
      reportVsPreviousPeriod: "vs previous period",
      reportVerificationTrend: "Verification activity",
      reportTrendDescription: "Documents by current status over time",
      reportSelectedPeriod: "Selected period",
      reportStatusBreakdown: "Status breakdown",
      reportCurrentStatus: "Current status of documents in this period",
      reportNoData: "No document verification data for this period",
      reportDocumentTypes: "Document types",
      reportDocumentTypesDescription: "Uploaded documents in this period",
      reportDecisionNoteTitle: "Verification is not a visa decision",
      reportDecisionNote:
        "Document checks support staff review. They do not prove identity or authenticity and do not determine visa approval.",
      reportViewApplications: "View applications",
      reportRecentTitle: "Recent application reports",
      reportRecentDescription:
        "Application-level status is derived from available verification records.",
      reportSearch: "Search applications",
      reportAllStatuses: "All statuses",
      reportApplication: "Application",
      reportDocuments: "Documents",
      reportScore: "Readiness",
      reportUpdated: "Last activity",
      reportNoMatchingRows: "No application reports match these filters.",
      reportShowingRows: "Showing {{count}} report(s)",
      users: "User Management",
      userManagementDescription: "View every registered account and update its role or status.",
      allUsers: "All users",
      searchUsers: "Search name, email or role",
      filterByRole: "Filter by role",
      allRoles: "All roles",
      name: "Name",
      role: "Role",
      accountStatus: "Account status",
      inactive: "Inactive",
      suspended: "Suspended",
      noUsers: "No registered users",
      noMatchingUsers: "No users match these filters",
      usersShown: "Showing {{shown}} of {{total}} users",
      userUpdated: "User updated.",
      userUpdateFailed: "Unable to update user.",
      settings: "Settings",
      search: "Search",
      notifications: "Notifications",
      language: "Language",
      logout: "Logout",
      totalCustomers: "Total Customers",
      totalVisaApplications: "Total Visa Applications",
      totalDocuments: "Total Documents",
      verifiedDocuments: "Verified Documents",
      pendingDocuments: "Pending Documents",
      missingDocuments: "Missing Documents",
      failedDocuments: "Failed Documents",
      avgCompleteness: "Avg. Completeness",
      recentActivities: "Recent Activities",
      applicationStatus: "Visa Status Distribution",
      verificationStatistics: "Document Verification",
      destinationStatistics: "Destination Country Statistics",
      completenessTrend: "Document Completeness Trends",
      monthlyApplications: "Monthly Visa Applications",
      status: "Status",
      recordFound: "Record(s)",
      addCustomer: "Add customer",
      fullName: "Full name",
      phone: "Phone",
      nationality: "Nationality",
      address: "Address",
      passportNumber: "Passport number",
      requestAiCheck: "Run AI Verification",
      recheckDocuments: "Recheck Documents",
      exportPdf: "Download PDF Report",
      documentChecklist: "Document checklist",
      noData: "No data available",
      applicationReadinessSummary: "Application readiness summary",
      liveDashboardTotals: "Live totals from the dashboard data",
      unavailable: "Unavailable",
      totalApplicationsLabel: "Total applications",
      completeApplicationsLabel: "Complete applications",
      incompleteApplicationsLabel: "Incomplete applications",
      averageReadinessLabel: "Average readiness score",
      privateDocumentStorage: "Private document storage",
      jwtAuthorization: "JWT authorization",
      rateLimiting: "Rate limiting",
      languageSupport: "Khmer & English support",
      enabled: "Enabled",
      adminRole: "Admin",
      staffRole: "Staff",
      customerRole: "Customer",
      authorizedProfile: "Authorized access profile",
      aiPoweredVisa: "AI-powered visa document intelligence",
      visaHeroLead: "AI-Powered",
      visaHeroFocus: "Visa",
      visaHeroSecondLine: "Document Intelligence",
      account: "Account",
      switchToLight: "Switch to light mode",
      switchToDark: "Switch to dark mode",
      secureWorkflow: "Secure workflow",
      workflowDescription:
        "Document OCR, verification, scoring, and compliance-ready reporting.",
      alreadyHaveAccount: "Already have an account?",
      needAccount: "Need an account?",
      loginFailed: "Login failed",
      rememberMe: "Remember me",
      forgotPassword: "Forgot password?",
      passwordResetUnavailable:
        "Password reset is not configured. Please contact your administrator.",
      socialSignInUnavailable: "Or continue with",
      socialProviderSetupRequired:
        "Social sign-in requires provider OAuth setup.",
      showPassword: "Show password",
      hidePassword: "Hide password",
      newVisaApplication: "New visa application",
      applicantDetailsChecklist: "Applicant details and document checklist",
      requirementsFromAdmin:
        "Requirements come from Admin configuration for the selected visa type.",
      selectVisaCode: "Select VISA Type Code",
      applicantInformation: "Applicant Information",
      useExistingCustomer: "Use existing customer (optional)",
      enterNewApplicant: "Enter a new applicant",
      fullNameTitle: "Full Name",
      emailTitle: "Email",
      phoneTitle: "Phone",
      nationalityTitle: "Nationality",
      passportNumberTitle: "Passport Number",
      dateOfBirthTitle: "Date of Birth",
      addressTitle: "Address",
      visaDocumentChecklist: "Visa Document Checklist",
      requiredDocumentsReady: "required documents ready",
      requiredDocumentCompletion: "Required document completion",
      loadingRequirements: "Loading configured requirements…",
      noVisaRequirements:
        "No document requirements are configured for this visa type. An Admin must configure them before an application can be created.",
      required: "Required",
      optional: "Optional",
      maximum: "Maximum",
      iHaveThisDocument: "I have this document",
      documentAvailabilityNote:
        "Mark whether each required document is available, then upload it. Uploaded files are checked during application verification; the readiness percentage reflects attached or uploaded files.",
      uploading: "Uploading…",
      uploadedSecurely: "Uploaded securely",
      requiredFileMessage: "A file is required for this document.",
      optionalFileMessage: "Choose a file or uncheck this optional document.",
      markRequiredMessage: "Mark this required document and attach its file.",
      documentUploadSummary: "Document Upload Summary",
      totalRequired: "Total required",
      uploadedDocuments: "Uploaded documents",
      readingDocument: "Reading document...",
      ocrCompletedReady: "OCR completed. Ready for verification.",
      photoOcrNotRequired: "Photo checks are used; OCR is not required.",
      bankStatementFieldCheck: "Required bank fields extracted",
      bankStatementCheckUnavailable: "Not available",
      bankScore:
        "Bank score: {{points}} / {{maximum}} points ({{percentage}}%)",
      bankScoreUnavailable:
        "Bank score unavailable · maximum {{maximum}} points",
      documentReadingFailed: "Document reading failed. Please retry.",
      ocrPagesProgress: "Pages processed: {{processed}} / {{total}}",
      ocrProgressPercent: "{{percent}}% processed",
      retryOcr: "Retry reading",
      ocrVerificationBlocked:
        "Wait for all uploaded documents to finish reading before verification.",
      documentsReadyCount: "{{count}} / {{total}} documents have a file",
      filesAttached: "Files attached",
      missingRequired: "Missing required",
      optionalDocumentsUploaded: "Optional documents uploaded",
      optionalFilesAttached: "Optional files attached",
      completion: "Completion",
      totalPoints: "Total points",
      pointsToPercentage: "{{earned}} / {{total}} points → {{percentage}}%",
      startAnotherApplication: "Start another application",
      creatingApplication: "Creating application…",
      createVisaApplication: "Create Visa Application",
      createAndVerifyApplication: "Create application & verify",
      retryFailedUploads: "Retry failed uploads",
      newCustomer: "1. Customer",
      existingCustomer: "Existing customer",
      createNewCustomer: "Create new customer",
      selectCustomer: "Select customer",
      creating: "Creating…",
      createSelectCustomer: "Create and select customer",
      addFile: "Add file",
      uploadFailed: "Upload failed",
      preview: "Preview",
      download: "Download",
      replace: "Replace",
      remove: "Remove",
      verify: "Verify",
      reject: "Reject",
      requirementsChecklist: "Requirements Checklist",
      checklistDescription:
        "Review each required item, confirm you have it, then attach the document. You can still submit with missing files.",
      documentCompletion: "Document completion",
      checklistItems: "checklist items",
      checklistItemsCount: "{{count}} documents required for this application",
      applicationSummary: "Application summary",
      nextStep: "Next step",
      nextStepMissing:
        "Upload the remaining required documents, then submit for verification.",
      nextStepReady:
        "Your checklist is ready. Submit it for document verification.",
      backToApplications: "Back to applications",
      loadingChecklist: "Loading application checklist…",
      retry: "Retry",
      applicationNotFound: "The requested application could not be found.",
      complete: "complete",
      completed: "completed",
      requiredMissing: "required missing",
      missingRequiredPrefix: "Missing required:",
      addDocumentFile: "Add File",
      uploaded: "Uploaded",
      notUploaded: "Not uploaded",
      reuploadRequested: "Re-upload requested:",
      submitForVerification: "Submit for Verification",
      documentReadiness: "Verify:",
      documentCheck: "Document check:",
      unableLoadOptions: "Unable to load application options",
      unableLoadChecklist: "Unable to load the application checklist",
      deleteApplication: "Delete application",
      confirmDeleteApplication:
        "Permanently delete application {{applicationNumber}} and all linked documents?",
      unableDeleteApplication: "Unable to delete the application",
      unableOpenDocument: "Unable to open document",
      unableSaveReview: "Unable to save review",
      unableSubmitVerification: "Unable to submit for verification",
      verificationOutcome: "Automated document check result",
      readinessScore: "Document completeness score",
      noApprovalPrediction:
        "Document check only · not an authenticity or visa decision.",
      automatedDocumentCheckExplanation:
        "OCR completed and this document is included in the automated completeness check. Authenticity is not confirmed.",
      verificationPassed: "Document check complete",
      verificationFailed: "Document needs correction",
      verificationMissing: "Required documents are missing",
      statusPass: "Passed",
      statusFail: "Failed",
      statusMissing: "Missing",
      statusWarning: "Needs correction",
      statusNotApplicable: "Not applicable",
      checksToReview: "Items needing attention",
      allChecksPassed: "All configured document checks passed.",
      passportDateMismatch:
        "Passport date of birth does not match applicant information.",
      passportExpiryInvalid:
        "Passport must remain valid for at least 6 months after the return date.",
      bankPeriodInvalid:
        "Bank statement must cover at least 3 calendar months.",
      bankBalanceLow: "Bank statement closing balance is below USD 4,000.",
      bankMonthlySummary: "Three-month transaction summary",
      bankMonthlySummaryHelp:
        "Enter the 3 months from oldest to newest. For each month: opening balance + deposits − withdrawals should equal closing balance. Amounts must use the statement currency.",
      oldestMonth: "Oldest month",
      middleMonth: "Middle month",
      newestMonth: "Newest month",
      monthlyOpeningBalance: "Opening balance",
      totalMonthlyCredits: "Total deposits",
      totalMonthlyDebits: "Total withdrawals",
      monthlyClosingBalance: "Closing balance",
      bankSummaryMissing:
        "Enter opening balance, total deposits, withdrawals, and closing balance for all 3 months.",
      bankSummaryInvalid:
        "Complete all non-negative monthly balances, deposits, and withdrawals.",
      bankReconciliationReview:
        "Monthly totals do not reconcile with the statement balances; automated checks could not confirm the balance.",
      bankBalanceDeclineReview:
        "The balance declined during the 3-month period; this is an automated risk warning.",
      fixedDepositAmountLow: "Fixed deposit is below USD 4,000.",
      manualCheckExplanation:
        "Automated checks could not reliably assess this document. OCR results do not establish authenticity.",
      runVerification: "Run document verification",
      verificationRunning: "Checking documents…",
      useAcceptedFile: "Use an accepted file",
      upTo: "up to",
      mb: "MB",
      reasonForRejection: "Reason for rejection / re-upload request:",
      configurationWorkspace: "CONFIGURATION WORKSPACE",
      visaRequirementsTitle: "VISA Requirements",
      manageVisaPrograms:
        "Manage visa programs and their document requirements in one place.",
      createVisaCode: "Create VISA code",
      visaRequirementSummary: "VISA requirement summary",
      totalVisaCodes: "Total VISA codes",
      activePrograms: "active programs",
      countries: "Countries",
      configuredDestinations: "Configured destinations",
      requirementsCountLabel: "Requirements",
      acrossActivePrograms: "Across active programs",
      editVisaRequirements: "Edit VISA requirements",
      createVisaRequirements: "Create VISA requirements",
      configureTravelDetails:
        "Configure the travel details and requirements for a visa program.",
      cancelEdit: "Cancel edit",
      country: "Country",
      selectCountry: "Select a country",
      addNewCountry: "+ Add new country",
      countryName: "Country name",
      iso2Code: "ISO-2 code",
      countryFlag: "Country flag",
      countryNationalityCode: "Country nationality code",
      reusedCountry: "already uses this ISO code and will be reused.",
      newCountryVisaChecklistSave:
        "New country, VISA code, and checklist save together.",
      travelPurpose: "Travel purpose",
      visaDuration: "Visa duration",
      allowedStay: "Allowed stay",
      days: "days",
      entryType: "Entry type",
      nationalityCode: "Nationality code",
      generatedVisaCode: "Generated VISA code",
      documentRequirements: "Document requirements",
      verifiedPolicyOnly:
        "Only add requirements provided by your agency's verified policy.",
      requirementName: "Requirement name",
      acceptedFormats: "Accepted formats",
      points: "Points",
      displayOrder: "Display order",
      multipleFiles: "Multiple files",
      photo: "Photo",
      enabledLabel: "Enabled",
      addRequirement: "Add requirement",
      additionalNotes: "Additional notes",
      optionalInternalGuidance:
        "Optional internal guidance for this visa program",
      reset: "Reset",
      saving: "Saving…",
      saveChanges: "Save changes",
      createVisaRequirementsButton: "Create VISA requirements",
      visaCodeDirectory: "VISA code directory",
      browseManagePrograms:
        "Browse, filter, and manage configured visa programs.",
      records: "records",
      searchVisaCountry: "Search VISA code or country",
      clearSearch: "Clear search",
      filterCountry: "Filter by country",
      allCountries: "All countries",
      filterVisaType: "Filter by visa type",
      allPurposes: "All purposes",
      filterVisaStatus: "Filter by VISA status",
      activeOnly: "Active only",
      allStatuses: "All statuses",
      archivedOnly: "Archived only",
      sortByUpdated: "Sort by updated date",
      latestUpdated: "Latest updated",
      loadingVisaCodes: "Loading VISA codes",
      visaCode: "VISA code",
      countryColumn: "Country",
      travelPurposeColumn: "Travel purpose",
      requirementsColumn: "Requirements",
      lastUpdated: "Last updated",
      actions: "Actions",
      active: "Active",
      archived: "Archived",
      viewDetails: "View details",
      edit: "Edit",
      duplicate: "Duplicate",
      archive: "Archive",
      activate: "Activate",
      delete: "Delete",
      showing: "Showing",
      of: "of",
      page: "Page",
      previousPage: "Previous page",
      nextPage: "Next page",
      noMatchingVisaCodes: "No matching VISA codes",
      noVisaCodesConfigured: "No VISA codes configured",
      tryDifferentSearch: "Try another search or clear the selected filters.",
      createProfileDirectory:
        "Create a VISA requirements profile to populate this directory.",
      clearFilters: "Clear filters",
      requirementChangeHistory: "Requirement change history",
      visaProgramDetails: "VISA PROGRAM DETAILS",
      closeDetails: "Close details",
      visaLabel: "visa",
      configuredRequirements: "Configured requirements",
      items: "items",
      noActiveRequirements: "No active document requirements are configured.",
      noAdditionalNotes: "No additional notes.",
      createdDate: "Created",
      updatedDate: "Updated",
      effective: "Effective",
      deleteVisaConfirm: "Delete this VISA code?",
      deleteVisaExplanation:
        "and its configured requirements will be deleted when safe. Records referenced by historical applications will be archived instead.",
      cancel: "Cancel",
      deleting: "Deleting…",
      confirmDelete: "Confirm delete",
      visaRequirementsUpdated: "VISA requirements updated.",
      archivedVisaRestored:
        "Archived VISA code restored and requirements updated.",
      existingVisaRequirementsUpdated: "Existing VISA requirements updated.",
      dismissNotification: "Dismiss notification",
      removeRequirement: "Remove requirement",
      recordsShown: "Showing",
      alreadyExists: "already exists.",
      applicationReadiness: "Application readiness summary",
      liveDashboardData: "Live totals from the dashboard data",
      unavailableTitle: "PDF report generation is not available yet",
      summaryTotalApplications: "Total applications",
      summaryCompleteApplications: "Complete applications",
      summaryIncompleteApplications: "Incomplete applications",
      summaryAverageReadiness: "Average readiness score",
      fileExceedsLimit: "File exceeds the upload limit of",
      dragDropFiles: "Drag and drop files here",
      applicationForDocument: "Application for document",
      selectApplication: "Select application",
      documentRequirement: "Document requirement",
      selectRequirement: "Select requirement",
      conditionalOptional: "Conditional/optional",
      uploadDocument: "Upload document",
      unableLoadDocumentRequirements: "Unable to load document requirements",
      fileExceeds: "File exceeds the",
      someUploadsFailed:
        "Some documents could not be uploaded. Correct the errors and retry.",
      applicationCreatedFilesUploaded:
        "Visa application created and documents uploaded.",
      documentsUploaded: "Documents uploaded.",
      adminMustConfigure:
        "An Admin must configure document requirements for this visa type first.",
      attachAllCheckedDocuments:
        "Select and attach a file for every required document and every checked optional document.",
      unableCreateVisaApplication: "Unable to create the visa application",
      unableCreateCustomer: "Unable to create customer",
      unableReplaceDocument: "Unable to replace document",
      unableRemoveDocument: "Unable to remove document",
      multipleFilesAllowed: "Multiple files allowed",
      oneFile: "One file",
      validPassportPlaceholder: "e.g. Valid passport",
      exampleKhm: "e.g. KHM",
      visaClass: "Visa class",
      effectiveDate: "Effective date",
      officialSourceUrl: "Official source URL",
      selectValidDetails: "Select valid details to generate a code",
      uniqueCodeChecked: "Unique code checked on save",
      entryLabel: "entry",
      actionsFor: "Actions for",
      viewRequirementHistory: "View history for",
      visaRequirementsCreated: "VISA requirements created.",
      unableLoadVisaRequirements: "Unable to load visa requirements.",
      unableDuplicateVisa: "Unable to duplicate VISA code.",
      visaStatusUpdated: "VISA status updated.",
      unableUpdateVisaStatus: "Unable to update VISA status.",
      visaCodeDeleted: "VISA code deleted.",
      unableDeleteVisa: "Unable to delete VISA code.",
      unableLoadHistory: "Unable to load requirement history.",
      unableRetry: "Please retry.",
      tourist: "Tourist",
      business: "Business",
      student: "Student",
      work: "Work",
      transit: "Transit",
      family: "Family",
      single: "Single",
      double: "Double",
      multiple: "Multiple",
      "30Days": "30 Days",
      "60Days": "60 Days",
      "90Days": "90 Days",
      "180Days": "180 Days",
      "1Year": "1 Year",
      "2Years": "2 Years",
      fileRequired: "File is required",
      countCharacters: "characters",
      customer: "Customer",
      pending: "Pending",
      rejected: "Rejected",
      verified: "Verified",
      draft: "Draft",
      underVerification: "Under verification",
      readyToUpload: "Ready to upload",
      uploadFailedStatus: "Upload failed",
      reviewActionCreate: "Created",
      reviewActionUpdate: "Updated",
      reviewActionDelete: "Deleted",
      invalidAcceptedFormats:
        "“{{name}}” must use PDF, JPG, JPEG, or PNG formats.",
      activateVisaBeforeEdit:
        "Activate this VISA code before editing its requirements.",
      validCountryAndIso:
        "Enter a country name and a valid two-letter ISO code.",
      chooseCountryBeforeSave: "Choose a country before saving.",
      checkVisaDetails:
        "Check the duration, stay length, class, and nationality code.",
      completeEachRequirement:
        "Complete each requirement with a non-negative points value and a whole-number display order.",
      addAtLeastOneRequirement: "Add at least one document requirement.",
      requirementNamesUnique:
        "Each document requirement must have a unique name.",
      chooseActiveCountry: "Choose a valid active country before saving.",
      partialRequirementsUpdate:
        "VISA code {{code}} was saved, but its requirements were only partially updated. {{error}}",
      reviewRequirementsRetry: "Review the requirements and retry.",
      countrySavedVisaFailed:
        "Country {{country}} was saved, but VISA code {{code}} could not be completed. Refresh the page to retry. {{error}}",
      countrySavedFormFailed:
        "Country {{country}} was saved, but the VISA form was not completed. {{error}}",
      pleaseRetry: "Please retry.",
      unableSaveVisaRequirements: "Unable to save VISA requirements.",
      createdVisaCode: "Created {{code}}.",
      travelStartDate: "Travel start date",
      returnDate: "Return date",
      japanTravelDatesRequired:
        "Travel start and return dates are required for this Japan visa.",
      dateComponentsOutOfRange:
        "Day must be 01–31 and month must be 01–12.",
      invalidCalendarDate: "Enter a real calendar date.",
      returnDateAfterDeparture:
        "Return date must be after the travel start date.",
      ocrCandidateReview:
        "OCR-filled values are candidates. Confirm or correct them; OCR does not confirm document authenticity.",
      ocrExtracted: "OCR extracted text ({{confidence}}% confidence).",
      ocrManualReview: "OCR results need further automated checks.",
      passportDateOfBirth: "Date of birth shown on passport",
      passportExpiryDate: "Passport expiry date",
      statementStartDate: "Statement period start date",
      statementEndDate: "Statement period end date",
      closingBalanceUsd: "Closing balance",
      currency: "Currency (e.g. USD)",
      depositAmount: "Fixed deposit amount",
      maturityDate: "Maturity date",
      saveExtractedValues: "Save document values",
      unableSaveExtractedValues: "Unable to save document values",
      passportDatesRule:
        "Passport birth date must match applicant information and passport expiry must be at least 6 months after return.",
      bankStatementRule:
        "Statement must cover at least 3 calendar months and show a USD closing balance of at least $4,000.",
      conditionalRequirement: "Conditional",
      manualReview: "Manual review",
    },
  },
  kh: {
    translation: {
      appName: "GLOBAL EAGLE",
      email: "អ៊ីមែល",
      password: "ពាក្យសម្ងាត់",
      login: "ចូលប្រើ",
      register: "ចុះឈ្មោះ",
      welcomeBack: "សូមស្វាគមន៍",
      signInToContinue: "ចូលប្រើដើម្បីបន្តទៅកាន់ផ្ទាំងការងាររបស់ភ្នាក់ងារអ្នក",
      overview: "ទិដ្ឋភាពទូទៅ",
      dashboard: "ផ្ទាំងគ្រប់គ្រង",
      visaApplicationsTitle: "ពាក្យសុំទិដ្ឋាការ",
      visaDashboard: "ផ្ទាំងគ្រប់គ្រងទិដ្ឋាការ",
      applicationsAndVerification: "ពាក្យសុំ និងការផ្ទៀងផ្ទាត់ឯកសារ",
      dashboardDescription:
        "តាមដានភាពរួចរាល់ ពិនិត្យឯកសារដែលបានផ្ទុកឡើង និងគ្រប់គ្រងពាក្យសុំទិដ្ឋាការទាំងអស់នៅកន្លែងតែមួយ។",
      newApplication: "ពាក្យសុំថ្មី",
      loading: "កំពុងផ្ទុក...",
      applicationPipeline: "ដំណើរការពាក្យសុំ · ស្ថានភាពពាក្យសុំនីមួយៗ",
      personalInfo: "ព័ត៌មានផ្ទាល់ខ្លួន",
      visaAndTravel: "ទិដ្ឋាការ និងការធ្វើដំណើរ",
      reviewAndSubmit: "ពិនិត្យ និងដាក់ស្នើ",
      applicantsCount: "អ្នកដាក់ពាក្យ {{count}} នាក់",
      configuredApplications: "ពាក្យសុំ {{count}}",
      documentsCount: "ឯកសារបានផ្ទុកឡើង {{count}}",
      awaitingReview: "រង់ចាំពិនិត្យ {{count}}",
      documentReadinessByApplication: "ភាពរួចរាល់ឯកសារតាមពាក្យសុំ",
      searchApplications: "ស្វែងរកអ្នកដាក់ពាក្យ ឬលេខពាក្យសុំ",
      filterByStatus: "តម្រងតាមស្ថានភាព",
      filterByVisaType: "តម្រងតាមប្រភេទទិដ្ឋាការ",
      allVisaTypes: "គ្រប់ប្រភេទទិដ្ឋាការ",
      applicationsShown: "ពាក្យសុំ",
      latestApplicationsNote: "ពាក្យសុំថ្មីៗ",
      needsAttention: "ត្រូវយកចិត្តទុកដាក់",
      reviewQueue: "ជួររង់ចាំពិនិត្យ",
      documentsAwaitingReview:
        "ឯកសារ {{count}} កំពុងរង់ចាំការផ្ទៀងផ្ទាត់ស្វ័យប្រវត្តិ",
      openReviewQueue: "បើកជួររង់ចាំពិនិត្យ",
      reuploadRequestedTitle: "ស្នើសុំផ្ទុកឡើងវិញ",
      documentsNeedReupload: "ឯកសារ {{count}} ត្រូវការឯកសារថ្មីពីអ្នកដាក់ពាក្យ",
      lowReadiness: "ភាពរួចរាល់ទាប",
      applicationsBelowReadiness: "ពាក្យសុំ {{count}} មានឯកសារបំពេញតិចជាង ៥០%",
      readinessDisclaimer:
        "ភាពរួចរាល់វាស់តែភាពពេញលេញនៃឯកសារប៉ុណ្ណោះ។ វាមិនមែនជាការព្យាករណ៍ការអនុម័តទិដ្ឋាការទេ។",
      customers: "អតិថិជន",
      applications: "ពាក្យសុំ Visa",
      documents: "ឯកសារ",
      verification: "ការពិនិត្យ AI",
      requirements: "ការគ្រប់គ្រងតម្រូវការ",
      reports: "របាយការណ៍",
      reportTitle: "របាយការណ៍ផ្ទៀងផ្ទាត់",
      reportDescription:
        "តាមដានសកម្មភាពផ្ទៀងផ្ទាត់ឯកសារ និងពិនិត្យរបាយការណ៍ពាក្យសុំ។",
      reportDateRange: "ចន្លោះពេល",
      reportLast7Days: "៧ ថ្ងៃចុងក្រោយ",
      reportLast30Days: "៣០ ថ្ងៃចុងក្រោយ",
      reportLast90Days: "៩០ ថ្ងៃចុងក្រោយ",
      reportLast12Months: "១២ ខែចុងក្រោយ",
      reportExport: "នាំចេញ CSV",
      reportSummary: "សង្ខេបការផ្ទៀងផ្ទាត់",
      reportTotalVerifications: "ឯកសារសរុប",
      reportVerified: "បានផ្ទៀងផ្ទាត់",
      reportRejected: "បានបដិសេធ",
      reportPending: "កំពុងរង់ចាំ",
      reportNoPreviousData: "គ្មានទិន្នន័យពីរយៈពេលមុន",
      reportVsPreviousPeriod: "ធៀបនឹងរយៈពេលមុន",
      reportVerificationTrend: "សកម្មភាពផ្ទៀងផ្ទាត់",
      reportTrendDescription: "ឯកសារតាមស្ថានភាពបច្ចុប្បន្នតាមពេលវេលា",
      reportSelectedPeriod: "រយៈពេលដែលបានជ្រើស",
      reportStatusBreakdown: "ការបែងចែកស្ថានភាព",
      reportCurrentStatus: "ស្ថានភាពបច្ចុប្បន្នរបស់ឯកសារក្នុងរយៈពេលនេះ",
      reportNoData: "គ្មានទិន្នន័យផ្ទៀងផ្ទាត់ឯកសារក្នុងរយៈពេលនេះទេ",
      reportDocumentTypes: "ប្រភេទឯកសារ",
      reportDocumentTypesDescription: "ឯកសារដែលបានផ្ទុកក្នុងរយៈពេលនេះ",
      reportDecisionNoteTitle: "ការផ្ទៀងផ្ទាត់មិនមែនជាការសម្រេចទិដ្ឋាការទេ",
      reportDecisionNote:
        "ការត្រួតពិនិត្យឯកសារជួយដល់បុគ្គលិកពិនិត្យប៉ុណ្ណោះ។ វាមិនបញ្ជាក់អត្តសញ្ញាណ ឬភាពពិតប្រាកដ និងមិនកំណត់ការអនុម័តទិដ្ឋាការទេ។",
      reportViewApplications: "មើលពាក្យសុំ",
      reportRecentTitle: "របាយការណ៍ពាក្យសុំថ្មីៗ",
      reportRecentDescription:
        "ស្ថានភាពពាក្យសុំត្រូវបានគណនាពីកំណត់ត្រាផ្ទៀងផ្ទាត់ដែលមាន។",
      reportSearch: "ស្វែងរកពាក្យសុំ",
      reportAllStatuses: "គ្រប់ស្ថានភាព",
      reportApplication: "ពាក្យសុំ",
      reportDocuments: "ឯកសារ",
      reportScore: "ភាពរួចរាល់",
      reportUpdated: "សកម្មភាពចុងក្រោយ",
      reportNoMatchingRows: "គ្មានរបាយការណ៍ពាក្យសុំដែលត្រូវនឹងតម្រងទេ។",
      reportShowingRows: "បង្ហាញរបាយការណ៍ {{count}}",
      users: "អ្នកប្រើ",
      userManagementDescription: "មើលគណនីដែលបានចុះឈ្មោះទាំងអស់ និងកែប្រែតួនាទី ឬស្ថានភាព។",
      allUsers: "អ្នកប្រើទាំងអស់",
      searchUsers: "ស្វែងរកឈ្មោះ អ៊ីមែល ឬតួនាទី",
      filterByRole: "តម្រងតាមតួនាទី",
      allRoles: "តួនាទីទាំងអស់",
      name: "ឈ្មោះ",
      role: "តួនាទី",
      accountStatus: "ស្ថានភាពគណនី",
      inactive: "អសកម្ម",
      suspended: "ផ្អាក",
      noUsers: "មិនទាន់មានអ្នកប្រើដែលបានចុះឈ្មោះ",
      noMatchingUsers: "គ្មានអ្នកប្រើដែលត្រូវនឹងតម្រងទាំងនេះទេ",
      usersShown: "បង្ហាញ {{shown}} ក្នុងចំណោម {{total}} អ្នកប្រើ",
      userUpdated: "បានធ្វើបច្ចុប្បន្នភាពអ្នកប្រើ។",
      userUpdateFailed: "មិនអាចធ្វើបច្ចុប្បន្នភាពអ្នកប្រើបានទេ។",
      settings: "ការកំណត់",
      search: "ស្វែងរក",
      notifications: "ការជូនដំណឹង",
      language: "ភាសា",
      logout: "ចាកចេញ",
      totalCustomers: "អតិថិជនសរុប",
      totalVisaApplications: "ពាក្យសុំ Visa សរុប",
      totalDocuments: "ឯកសារ Total",
      verifiedDocuments: "ឯកសារដែលបានផ្ទៀងផ្ទាត់",
      pendingDocuments: "ឯកសារដែលកំពុងរង់ចាំ",
      missingDocuments: "ឯកសារខ្វះ",
      failedDocuments: "ឯកសារដែលបរាជ័យ",
      avgCompleteness: "មធ្យមភាគដែលបំពេញ",
      recentActivities: "សកម្មភាពថ្មីៗ",
      applicationStatus: "ការបែងចែកស្ថានភាពទិដ្ឋាការ",
      verificationStatistics: "ស្ថិតិការពិនិត្យឯកសារ",
      destinationStatistics: "ស្ថិតិប្រទេសគោលដៅ",
      completenessTrend: "លំនាំភាពបំពេញ",
      monthlyApplications: "ពាក្យសុំ Visa តាមខែ",
      status: "ស្ថានភាព",
      recordFound: "កំណត់ត្រា",
      addCustomer: "បន្ថែមអតិថិជន",
      fullName: "ឈ្មោះពេញ",
      phone: "លេខទូរសព្ទ",
      nationality: "សញ្ជាតិ",
      address: "អាសយដ្ឋាន",
      passportNumber: "លេខលិខិតឆ្លងដែន",
      requestAiCheck: "ដំណើរការពិនិត្យ AI",
      recheckDocuments: "ពិនិត្យឡើងវិញ",
      exportPdf: "ទាញយក PDF",
      documentChecklist: "របាយការណ៍ឯកសារ",
      noData: "មិនមានទិន្នន័យ",
      totalApplications: "ពាក្យសុំសរុប",
      approvedApplications: "ពាក្យសុំដែលបានអនុម័ត",
      rejectedApplications: "ពាក្យសុំដែលបានបដិសេធ",
      completeApplications: "ពាក្យសុំបានបំពេញ",
      incompleteApplications: "ពាក្យសុំមិនទាន់បំពេញ",
      pendingVerification: "កំពុងរង់ចាំការផ្ទៀងផ្ទាត់",
      averageReadiness: "ពិន្ទុត្រៀមខ្លួនជាមធ្យម",
      recentApplications: "ពាក្យសុំថ្មីៗ",
      applicant: "អ្នកដាក់ពាក្យ",
      visaType: "ប្រភេទទិដ្ឋាការ",
      readiness: "ភាពត្រៀមខ្លួន",
      created: "បានបង្កើត",
      applicationDate: "កាលបរិច្ឆេទពាក្យសុំ",
      viewReport: "មើលរបាយការណ៍",
      noApplications: "មិនទាន់មានពាក្យសុំទេ",
      visaTypeDistribution: "ការបែងចែកតាមប្រភេទទិដ្ឋាការ",
      readinessTrend: "និន្នាការពិន្ទុត្រៀមខ្លួន",
      applicationTrend: "និន្នាការពាក្យសុំ",
      applicationReadinessSummary: "សង្ខេបភាពត្រៀមខ្លួននៃពាក្យសុំ",
      liveDashboardTotals: "ទិន្នន័យសរុបផ្ទាល់ពីផ្ទាំងគ្រប់គ្រង",
      unavailable: "មិនទាន់មាន",
      totalApplicationsLabel: "ពាក្យសុំសរុប",
      completeApplicationsLabel: "ពាក្យសុំបានបំពេញ",
      incompleteApplicationsLabel: "ពាក្យសុំមិនទាន់បំពេញ",
      averageReadinessLabel: "ពិន្ទុត្រៀមខ្លួនជាមធ្យម",
      privateDocumentStorage: "ការរក្សាទុកឯកសារជាឯកជន",
      jwtAuthorization: "ការអនុញ្ញាត JWT",
      rateLimiting: "ការកំណត់ចំនួនសំណើ",
      languageSupport: "គាំទ្រភាសាខ្មែរ និងអង់គ្លេស",
      enabled: "បានបើក",
      adminRole: "អ្នកគ្រប់គ្រង",
      staffRole: "បុគ្គលិក",
      customerRole: "អតិថិជន",
      authorizedProfile: "កម្រងសិទ្ធិចូលប្រើ",
      aiPoweredVisa: "ប្រព័ន្ធឆ្លាតវៃឯកសារទិដ្ឋាការដោយ AI",
      visaHeroLead: "ប្រព័ន្ធ AI",
      visaHeroFocus: "ទិដ្ឋាការ",
      visaHeroSecondLine: "ឆ្លាតវៃឯកសារ",
      account: "គណនី",
      switchToLight: "ប្ដូរទៅរបៀបភ្លឺ",
      switchToDark: "ប្ដូរទៅរបៀបងងឹត",
      secureWorkflow: "ដំណើរការប្រកបដោយសុវត្ថិភាព",
      workflowDescription:
        "OCR ឯកសារ ការផ្ទៀងផ្ទាត់ ការដាក់ពិន្ទុ និងរបាយការណ៍តាមស្តង់ដារ។",
      alreadyHaveAccount: "មានគណនីរួចហើយឬ?",
      needAccount: "ត្រូវការបង្កើតគណនីឬ?",
      loginFailed: "ការចូលប្រើបានបរាជ័យ",
      rememberMe: "ចងចាំខ្ញុំ",
      forgotPassword: "ភ្លេចពាក្យសម្ងាត់?",
      passwordResetUnavailable:
        "មិនទាន់មានការកំណត់ស្តារពាក្យសម្ងាត់ទេ។ សូមទាក់ទងអ្នកគ្រប់គ្រង។",
      socialSignInUnavailable: "ឬបន្តជាមួយ",
      socialProviderSetupRequired:
        "ការចូលតាមគណនីសង្គមត្រូវការកំណត់ OAuth provider ជាមុន។",
      showPassword: "បង្ហាញពាក្យសម្ងាត់",
      hidePassword: "លាក់ពាក្យសម្ងាត់",
      newVisaApplication: "ពាក្យសុំទិដ្ឋាការថ្មី",
      applicantDetailsChecklist: "ព័ត៌មានអ្នកដាក់ពាក្យ និងបញ្ជីឯកសារ",
      requirementsFromAdmin:
        "តម្រូវការត្រូវបានកំណត់ដោយអ្នកគ្រប់គ្រងសម្រាប់ប្រភេទទិដ្ឋាការដែលបានជ្រើសរើស។",
      selectVisaCode: "ជ្រើសរើសកូដប្រភេទទិដ្ឋាការ",
      applicantInformation: "ព័ត៌មានអ្នកដាក់ពាក្យ",
      useExistingCustomer: "ប្រើអតិថិជនដែលមានស្រាប់ (មិនចាំបាច់)",
      enterNewApplicant: "បញ្ចូលអ្នកដាក់ពាក្យថ្មី",
      fullNameTitle: "ឈ្មោះពេញ",
      emailTitle: "អ៊ីមែល",
      phoneTitle: "លេខទូរសព្ទ",
      nationalityTitle: "សញ្ជាតិ",
      passportNumberTitle: "លេខលិខិតឆ្លងដែន",
      dateOfBirthTitle: "ថ្ងៃខែឆ្នាំកំណើត",
      addressTitle: "អាសយដ្ឋាន",
      visaDocumentChecklist: "បញ្ជីឯកសារទិដ្ឋាការ",
      requiredDocumentsReady: "នៃឯកសារចាំបាច់បានត្រៀមរួច",
      requiredDocumentCompletion: "ការបំពេញឯកសារចាំបាច់",
      loadingRequirements: "កំពុងផ្ទុកតម្រូវការដែលបានកំណត់…",
      noVisaRequirements:
        "មិនទាន់មានតម្រូវការឯកសារសម្រាប់ប្រភេទទិដ្ឋាការនេះទេ។ អ្នកគ្រប់គ្រងត្រូវកំណត់តម្រូវការមុននឹងបង្កើតពាក្យសុំ។",
      required: "ចាំបាច់",
      optional: "ស្រេចចិត្ត",
      maximum: "អតិបរមា",
      iHaveThisDocument: "ខ្ញុំមានឯកសារនេះ",
      documentAvailabilityNote:
        "សូមធីកឯកសារដែលអ្នកមាន មុនពេលផ្ទុកឡើង។ ឯកសារដែលបានផ្ទុកឡើងនឹងត្រូវបានពិនិត្យក្នុងដំណើរការផ្ទៀងផ្ទាត់ពាក្យសុំ។ ភាគរយបង្ហាញតាមឯកសារដែលបានភ្ជាប់ ឬផ្ទុកឡើង។",
      uploading: "កំពុងផ្ទុកឡើង…",
      uploadedSecurely: "បានផ្ទុកឡើងដោយសុវត្ថិភាព",
      requiredFileMessage: "ត្រូវការឯកសារនេះ។",
      optionalFileMessage: "ជ្រើសរើសឯកសារ ឬដោះធីកឯកសារស្រេចចិត្តនេះ។",
      markRequiredMessage: "សម្គាល់ឯកសារចាំបាច់នេះ ហើយភ្ជាប់ឯកសារ។",
      documentUploadSummary: "សង្ខេបការផ្ទុកឯកសារ",
      totalRequired: "ចំនួនឯកសារចាំបាច់",
      uploadedDocuments: "ឯកសារបានផ្ទុកឡើង",
      readingDocument: "កំពុងអានឯកសារ...",
      ocrCompletedReady: "ការអាន OCR បានបញ្ចប់។ អាចផ្ទៀងផ្ទាត់បាន។",
      photoOcrNotRequired: "ប្រើការត្រួតពិនិត្យរូបថត; មិនចាំបាច់ប្រើ OCR ទេ។",
      bankStatementFieldCheck: "វាលធនាគារចាំបាច់ដែល OCR បានស្រង់",
      bankStatementCheckUnavailable: "មិនមានទិន្នន័យ",
      bankScore:
        "ពិន្ទុរបាយការណ៍ធនាគារ៖ {{points}} / {{maximum}} ពិន្ទុ ({{percentage}}%)",
      bankScoreUnavailable:
        "មិនអាចគណនាពិន្ទុធនាគារបាន · អតិបរមា {{maximum}} ពិន្ទុ",
      documentReadingFailed: "ការអានឯកសារបរាជ័យ។ សូមព្យាយាមម្តងទៀត។",
      ocrPagesProgress: "ទំព័រដែលបានអាន៖ {{processed}} / {{total}}",
      ocrProgressPercent: "បានអាន {{percent}}%",
      retryOcr: "ព្យាយាមអានម្តងទៀត",
      ocrVerificationBlocked:
        "សូមរង់ចាំឱ្យការអានឯកសារទាំងអស់បញ្ចប់ មុនពេលផ្ទៀងផ្ទាត់។",
      documentsReadyCount: "មានឯកសារ {{count}} / {{total}}",
      filesAttached: "ឯកសារបានភ្ជាប់",
      missingRequired: "ឯកសារចាំបាច់ដែលខ្វះ",
      optionalDocumentsUploaded: "ឯកសារស្រេចចិត្តបានផ្ទុកឡើង",
      optionalFilesAttached: "ឯកសារស្រេចចិត្តបានភ្ជាប់",
      completion: "ការបំពេញ",
      totalPoints: "ពិន្ទុសរុប",
      pointsToPercentage: "{{earned}} / {{total}} ពិន្ទុ → {{percentage}}%",
      startAnotherApplication: "ចាប់ផ្តើមពាក្យសុំថ្មី",
      creatingApplication: "កំពុងបង្កើតពាក្យសុំ…",
      createVisaApplication: "បង្កើតពាក្យសុំទិដ្ឋាការ",
      createAndVerifyApplication: "បង្កើតពាក្យសុំ និងផ្ទៀងផ្ទាត់",
      retryFailedUploads: "ព្យាយាមផ្ទុកឯកសារដែលបរាជ័យម្តងទៀត",
      newCustomer: "១. អតិថិជន",
      existingCustomer: "អតិថិជនដែលមានស្រាប់",
      createNewCustomer: "បង្កើតអតិថិជនថ្មី",
      selectCustomer: "ជ្រើសរើសអតិថិជន",
      creating: "កំពុងបង្កើត…",
      createSelectCustomer: "បង្កើត និងជ្រើសរើសអតិថិជន",
      addFile: "បន្ថែមឯកសារ",
      uploadFailed: "ការផ្ទុកឡើងបានបរាជ័យ",
      preview: "មើលជាមុន",
      download: "ទាញយក",
      replace: "ជំនួស",
      remove: "លុបចេញ",
      verify: "ផ្ទៀងផ្ទាត់",
      reject: "បដិសេធ",
      requirementsChecklist: "បញ្ជីត្រួតពិនិត្យតម្រូវការ",
      checklistDescription:
        "ពិនិត្យឯកសារនីមួយៗ ធីកប្រសិនបើអ្នកមាន ហើយភ្ជាប់ឯកសារ។ អ្នកនៅតែអាចដាក់ស្នើបាន ទោះបីខ្វះឯកសារខ្លះក៏ដោយ។",
      documentCompletion: "ការបំពេញឯកសារ",
      checklistItems: "ធាតុក្នុងបញ្ជី",
      checklistItemsCount: "តម្រូវឱ្យមានឯកសារ {{count}} សម្រាប់ពាក្យសុំនេះ",
      applicationSummary: "សង្ខេបពាក្យសុំ",
      nextStep: "ជំហានបន្ទាប់",
      nextStepMissing:
        "សូមផ្ទុកឯកសារចាំបាច់ដែលនៅខ្វះ រួចដាក់ស្នើសម្រាប់ផ្ទៀងផ្ទាត់។",
      nextStepReady:
        "បញ្ជីឯកសាររួចរាល់ហើយ។ សូមដាក់ស្នើសម្រាប់ផ្ទៀងផ្ទាត់ឯកសារ។",
      backToApplications: "ត្រឡប់ទៅពាក្យសុំ",
      loadingChecklist: "កំពុងផ្ទុកបញ្ជីត្រួតពិនិត្យ…",
      retry: "ព្យាយាមម្តងទៀត",
      applicationNotFound: "រកមិនឃើញពាក្យសុំដែលបានជ្រើសទេ។",
      complete: "បានបំពេញ",
      completed: "បានបញ្ចប់",
      requiredMissing: "ឯកសារចាំបាច់ខ្វះ",
      missingRequiredPrefix: "ឯកសារចាំបាច់ដែលខ្វះ៖",
      addDocumentFile: "បន្ថែមឯកសារ",
      uploaded: "បានផ្ទុកឡើង",
      notUploaded: "មិនទាន់បានផ្ទុកឡើង",
      reuploadRequested: "ស្នើសុំផ្ទុកឡើងវិញ៖",
      submitForVerification: "ដាក់ស្នើសម្រាប់ផ្ទៀងផ្ទាត់",
      documentReadiness: "ផ្ទៀងផ្ទាត់៖",
      documentCheck: "ត្រួតពិនិត្យឯកសារ៖",
      unableLoadOptions: "មិនអាចផ្ទុកជម្រើសពាក្យសុំបានទេ",
      unableLoadChecklist: "មិនអាចផ្ទុកបញ្ជីត្រួតពិនិត្យពាក្យសុំបានទេ",
      deleteApplication: "លុបពាក្យសុំ",
      confirmDeleteApplication:
        "លុបពាក្យសុំ {{applicationNumber}} និងឯកសារដែលភ្ជាប់ទាំងអស់ជាអចិន្ត្រៃយ៍?",
      unableDeleteApplication: "មិនអាចលុបពាក្យសុំបានទេ",
      unableOpenDocument: "មិនអាចបើកឯកសារបានទេ",
      unableSaveReview: "មិនអាចរក្សាទុកការត្រួតពិនិត្យបានទេ",
      unableSubmitVerification: "មិនអាចដាក់ស្នើសម្រាប់ការផ្ទៀងផ្ទាត់បានទេ",
      verificationOutcome: "លទ្ធផលត្រួតពិនិត្យឯកសារស្វ័យប្រវត្តិ",
      readinessScore: "ពិន្ទុភាពពេញលេញឯកសារ",
      noApprovalPrediction:
        "ពិនិត្យឯកសារប៉ុណ្ណោះ · មិនមែនការបញ្ជាក់ភាពពិត ឬសម្រេចទិដ្ឋាការទេ។",
      automatedDocumentCheckExplanation:
        "OCR បានបញ្ចប់ ហើយឯកសារនេះត្រូវបានរាប់ក្នុងការត្រួតពិនិត្យភាពពេញលេញស្វ័យប្រវត្តិ។ មិនបានបញ្ជាក់ភាពពិតប្រាកដទេ។",
      verificationPassed: "ការត្រួតពិនិត្យឯកសារបានបញ្ចប់",
      verificationFailed: "ឯកសារត្រូវការកែតម្រូវ",
      verificationMissing: "ខ្វះឯកសារចាំបាច់",
      statusPass: "ឆ្លង",
      statusFail: "មិនឆ្លង",
      statusMissing: "ខ្វះ",
      statusWarning: "ត្រូវកែតម្រូវ",
      statusNotApplicable: "មិនអនុវត្ត",
      checksToReview: "ចំណុចត្រូវយកចិត្តទុកដាក់",
      allChecksPassed: "ការត្រួតពិនិត្យឯកសារដែលបានកំណត់ទាំងអស់បានឆ្លង។",
      passportDateMismatch:
        "ថ្ងៃកំណើតលើលិខិតឆ្លងដែនមិនត្រូវគ្នានឹងព័ត៌មានអ្នកដាក់ពាក្យទេ។",
      passportExpiryInvalid:
        "លិខិតឆ្លងដែនត្រូវមានសុពលភាពយ៉ាងតិច ៦ ខែបន្ទាប់ពីថ្ងៃត្រឡប់។",
      bankPeriodInvalid: "របាយការណ៍ធនាគារត្រូវគ្របដណ្តប់យ៉ាងតិច ៣ ខែ។",
      bankBalanceLow: "សមតុល្យបិទបញ្ជីធនាគារតិចជាង ៤,០០០ ដុល្លារអាមេរិក។",
      bankMonthlySummary: "សង្ខេបប្រតិបត្តិការរយៈពេល ៣ ខែ",
      bankMonthlySummaryHelp:
        "បញ្ចូល ៣ ខែពីខែចាស់ទៅខែថ្មី។ សម្រាប់ខែនីមួយៗ៖ សមតុល្យដើម + ប្រាក់ចូល − ប្រាក់ចេញ គួរតែស្មើសមតុល្យចុង។ ចំនួនទឹកប្រាក់ត្រូវប្រើរូបិយប័ណ្ណដូចក្នុងរបាយការណ៍។",
      oldestMonth: "ខែចាស់បំផុត",
      middleMonth: "ខែកណ្តាល",
      newestMonth: "ខែថ្មីបំផុត",
      monthlyOpeningBalance: "សមតុល្យដើមខែ",
      totalMonthlyCredits: "ប្រាក់ចូលសរុប",
      totalMonthlyDebits: "ប្រាក់ចេញសរុប",
      monthlyClosingBalance: "សមតុល្យចុងខែ",
      bankSummaryMissing:
        "សូមបញ្ចូលសមតុល្យដើម ប្រាក់ចូល ប្រាក់ចេញ និងសមតុល្យចុង សម្រាប់ខែទាំង ៣។",
      bankSummaryInvalid:
        "សូមបំពេញសមតុល្យ ប្រាក់ចូល និងប្រាក់ចេញប្រចាំខែដែលមិនមែនជាចំនួនអវិជ្ជមាន។",
      bankReconciliationReview:
        "ចំនួនប្រចាំខែមិនត្រូវគ្នានឹងសមតុល្យក្នុងរបាយការណ៍ទេ។ ការត្រួតពិនិត្យស្វ័យប្រវត្តិមិនអាចបញ្ជាក់សមតុល្យបាន។",
      bankBalanceDeclineReview:
        "សមតុល្យបានធ្លាក់ចុះក្នុងរយៈពេល ៣ ខែ។ នេះជាការព្រមានហានិភ័យដោយស្វ័យប្រវត្តិ។",
      fixedDepositAmountLow: "ប្រាក់បញ្ញើមានចំនួនតិចជាង ៤,០០០ ដុល្លារអាមេរិក។",
      manualCheckExplanation:
        "ការត្រួតពិនិត្យស្វ័យប្រវត្តិមិនអាចវាយតម្លៃឯកសារនេះបានច្បាស់លាស់ទេ។ លទ្ធផល OCR មិនបញ្ជាក់ភាពត្រឹមត្រូវពិតប្រាកដឡើយ។",
      runVerification: "ដំណើរការផ្ទៀងផ្ទាត់ឯកសារ",
      verificationRunning: "កំពុងពិនិត្យឯកសារ…",
      useAcceptedFile: "សូមប្រើប្រភេទឯកសារដែលបានអនុញ្ញាត",
      upTo: "រហូតដល់",
      mb: "មេកាបៃ",
      reasonForRejection: "មូលហេតុនៃការបដិសេធ/ស្នើសុំផ្ទុកឡើងវិញ៖",
      configurationWorkspace: "កន្លែងកំណត់រចនាសម្ព័ន្ធ",
      visaRequirementsTitle: "តម្រូវការទិដ្ឋាការ",
      manageVisaPrograms:
        "គ្រប់គ្រងកម្មវិធីទិដ្ឋាការ និងតម្រូវការឯកសារនៅកន្លែងតែមួយ។",
      createVisaCode: "បង្កើតកូដទិដ្ឋាការ",
      visaRequirementSummary: "សង្ខេបតម្រូវការទិដ្ឋាការ",
      totalVisaCodes: "កូដទិដ្ឋាការសរុប",
      activePrograms: "កម្មវិធីកំពុងដំណើរការ",
      countries: "ប្រទេស",
      configuredDestinations: "គោលដៅដែលបានកំណត់",
      requirementsCountLabel: "តម្រូវការ",
      acrossActivePrograms: "ក្នុងកម្មវិធីសកម្ម",
      editVisaRequirements: "កែសម្រួលតម្រូវការទិដ្ឋាការ",
      createVisaRequirements: "បង្កើតតម្រូវការទិដ្ឋាការ",
      configureTravelDetails:
        "កំណត់ព័ត៌មានការធ្វើដំណើរ និងតម្រូវការសម្រាប់កម្មវិធីទិដ្ឋាការ។",
      cancelEdit: "បោះបង់ការកែសម្រួល",
      country: "ប្រទេស",
      selectCountry: "ជ្រើសរើសប្រទេស",
      addNewCountry: "+ បន្ថែមប្រទេសថ្មី",
      countryName: "ឈ្មោះប្រទេស",
      iso2Code: "កូដ ISO-2",
      countryFlag: "ទង់ជាតិ",
      countryNationalityCode: "កូដសញ្ជាតិប្រទេស",
      reusedCountry: "ប្រើកូដ ISO នេះរួចហើយ ហើយនឹងត្រូវបានប្រើឡើងវិញ។",
      newCountryVisaChecklistSave:
        "ប្រទេសថ្មី កូដទិដ្ឋាការ និងបញ្ជីឯកសារ នឹងរក្សាទុករួមគ្នា។",
      travelPurpose: "គោលបំណងធ្វើដំណើរ",
      visaDuration: "រយៈពេលទិដ្ឋាការ",
      allowedStay: "រយៈពេលអនុញ្ញាតស្នាក់នៅ",
      days: "ថ្ងៃ",
      entryType: "ប្រភេទចូល",
      nationalityCode: "កូដសញ្ជាតិ",
      generatedVisaCode: "កូដទិដ្ឋាការដែលបានបង្កើត",
      documentRequirements: "តម្រូវការឯកសារ",
      verifiedPolicyOnly:
        "សូមបន្ថែមតែតម្រូវការដែលបានផ្ទៀងផ្ទាត់តាមគោលការណ៍របស់ភ្នាក់ងារអ្នកប៉ុណ្ណោះ។",
      requirementName: "ឈ្មោះតម្រូវការ",
      acceptedFormats: "ទម្រង់ឯកសារដែលអនុញ្ញាត",
      points: "ពិន្ទុ",
      displayOrder: "លំដាប់បង្ហាញ",
      multipleFiles: "អនុញ្ញាតឯកសារច្រើន",
      photo: "រូបថត",
      enabledLabel: "បើកប្រើ",
      addRequirement: "បន្ថែមតម្រូវការ",
      additionalNotes: "កំណត់ចំណាំបន្ថែម",
      optionalInternalGuidance:
        "ការណែនាំផ្ទៃក្នុងស្រេចចិត្តសម្រាប់កម្មវិធីទិដ្ឋាការនេះ",
      reset: "សម្អាត",
      saving: "កំពុងរក្សាទុក…",
      saveChanges: "រក្សាទុកការផ្លាស់ប្តូរ",
      createVisaRequirementsButton: "បង្កើតតម្រូវការទិដ្ឋាការ",
      visaCodeDirectory: "បញ្ជីកូដទិដ្ឋាការ",
      browseManagePrograms:
        "ស្វែងរក ត្រង និងគ្រប់គ្រងកម្មវិធីទិដ្ឋាការដែលបានកំណត់។",
      records: "កំណត់ត្រា",
      searchVisaCountry: "ស្វែងរកកូដទិដ្ឋាការ ឬប្រទេស",
      clearSearch: "សម្អាតការស្វែងរក",
      filterCountry: "ត្រងតាមប្រទេស",
      allCountries: "ប្រទេសទាំងអស់",
      filterVisaType: "ត្រងតាមប្រភេទទិដ្ឋាការ",
      allPurposes: "គោលបំណងទាំងអស់",
      filterVisaStatus: "ត្រងតាមស្ថានភាពទិដ្ឋាការ",
      activeOnly: "សកម្មប៉ុណ្ណោះ",
      allStatuses: "ស្ថានភាពទាំងអស់",
      archivedOnly: "បានទុកក្នុងប័ណ្ណសារប៉ុណ្ណោះ",
      sortByUpdated: "តម្រៀបតាមថ្ងៃកែប្រែ",
      latestUpdated: "កែប្រែថ្មីៗ",
      loadingVisaCodes: "កំពុងផ្ទុកកូដទិដ្ឋាការ",
      visaCode: "កូដទិដ្ឋាការ",
      countryColumn: "ប្រទេស",
      travelPurposeColumn: "គោលបំណងធ្វើដំណើរ",
      requirementsColumn: "តម្រូវការ",
      lastUpdated: "កែប្រែចុងក្រោយ",
      actions: "សកម្មភាព",
      active: "សកម្ម",
      archived: "បានទុកក្នុងប័ណ្ណសារ",
      viewDetails: "មើលព័ត៌មានលម្អិត",
      edit: "កែសម្រួល",
      duplicate: "ចម្លង",
      archive: "ទុកក្នុងប័ណ្ណសារ",
      activate: "ធ្វើឲ្យសកម្ម",
      delete: "លុប",
      showing: "កំពុងបង្ហាញ",
      of: "ក្នុងចំណោម",
      page: "ទំព័រ",
      previousPage: "ទំព័រមុន",
      nextPage: "ទំព័របន្ទាប់",
      noMatchingVisaCodes: "រកមិនឃើញកូដទិដ្ឋាការដែលត្រូវគ្នា",
      noVisaCodesConfigured: "មិនទាន់មានកូដទិដ្ឋាការទេ",
      tryDifferentSearch: "សាកល្បងស្វែងរកផ្សេង ឬសម្អាតតម្រងដែលបានជ្រើសរើស។",
      createProfileDirectory:
        "បង្កើតទម្រង់តម្រូវការទិដ្ឋាការ ដើម្បីបង្ហាញក្នុងបញ្ជីនេះ។",
      clearFilters: "សម្អាតតម្រង",
      requirementChangeHistory: "ប្រវត្តិការផ្លាស់ប្តូរតម្រូវការ",
      visaProgramDetails: "ព័ត៌មានលម្អិតកម្មវិធីទិដ្ឋាការ",
      closeDetails: "បិទព័ត៌មានលម្អិត",
      visaLabel: "ទិដ្ឋាការ",
      configuredRequirements: "តម្រូវការដែលបានកំណត់",
      items: "ធាតុ",
      noActiveRequirements: "មិនទាន់មានតម្រូវការឯកសារសកម្មទេ។",
      noAdditionalNotes: "មិនមានកំណត់ចំណាំបន្ថែមទេ។",
      createdDate: "បានបង្កើត",
      updatedDate: "បានកែប្រែ",
      effective: "មានប្រសិទ្ធភាព",
      deleteVisaConfirm: "លុបកូដទិដ្ឋាការនេះ?",
      deleteVisaExplanation:
        "និងតម្រូវការដែលបានកំណត់នឹងត្រូវលុប ប្រសិនបើមានសុវត្ថិភាព។ កំណត់ត្រាដែលប្រវត្តិពាក្យសុំយោងនឹងត្រូវទុកក្នុងប័ណ្ណសារជំនួសវិញ។",
      cancel: "បោះបង់",
      deleting: "កំពុងលុប…",
      confirmDelete: "បញ្ជាក់ការលុប",
      visaRequirementsUpdated: "បានកែប្រែតម្រូវការទិដ្ឋាការ។",
      archivedVisaRestored:
        "បានស្ដារកូដទិដ្ឋាការពីប័ណ្ណសារ និងកែប្រែតម្រូវការ។",
      existingVisaRequirementsUpdated:
        "បានកែប្រែតម្រូវការទិដ្ឋាការដែលមានស្រាប់។",
      dismissNotification: "បិទការជូនដំណឹង",
      removeRequirement: "លុបតម្រូវការ",
      recordsShown: "កំពុងបង្ហាញ",
      alreadyExists: "មានរួចហើយ។",
      applicationReadiness: "សង្ខេបភាពត្រៀមខ្លួននៃពាក្យសុំ",
      liveDashboardData: "ទិន្នន័យសរុបផ្ទាល់ពីផ្ទាំងគ្រប់គ្រង",
      unavailableTitle: "មុខងារបង្កើតរបាយការណ៍ PDF មិនទាន់មានទេ",
      summaryTotalApplications: "ពាក្យសុំសរុប",
      summaryCompleteApplications: "ពាក្យសុំបានបំពេញ",
      summaryIncompleteApplications: "ពាក្យសុំមិនទាន់បំពេញ",
      summaryAverageReadiness: "ពិន្ទុត្រៀមខ្លួនជាមធ្យម",
      fileExceedsLimit: "ឯកសារលើសពីទំហំផ្ទុកអតិបរមា",
      dragDropFiles: "អូស និងទម្លាក់ឯកសារនៅទីនេះ",
      applicationForDocument: "ពាក្យសុំសម្រាប់ឯកសារ",
      selectApplication: "ជ្រើសរើសពាក្យសុំ",
      documentRequirement: "តម្រូវការឯកសារ",
      selectRequirement: "ជ្រើសរើសតម្រូវការ",
      conditionalOptional: "មានលក្ខខណ្ឌ/ស្រេចចិត្ត",
      uploadDocument: "ផ្ទុកឯកសារឡើង",
      unableLoadDocumentRequirements: "មិនអាចផ្ទុកតម្រូវការឯកសារបានទេ",
      fileExceeds: "ឯកសារលើសពីទំហំអនុញ្ញាត",
      someUploadsFailed:
        "ឯកសារមួយចំនួនមិនអាចផ្ទុកឡើងបានទេ។ សូមកែបញ្ហា ហើយព្យាយាមម្តងទៀត។",
      applicationCreatedFilesUploaded:
        "បានបង្កើតពាក្យសុំទិដ្ឋាការ និងផ្ទុកឯកសារឡើងរួចរាល់។",
      documentsUploaded: "បានផ្ទុកឯកសារឡើងរួចរាល់។",
      adminMustConfigure:
        "អ្នកគ្រប់គ្រងត្រូវកំណត់តម្រូវការឯកសារសម្រាប់ប្រភេទទិដ្ឋាការនេះជាមុន។",
      attachAllCheckedDocuments:
        "សូមជ្រើសរើស និងភ្ជាប់ឯកសារចាំបាច់ទាំងអស់ និងឯកសារស្រេចចិត្តដែលបានធីក។",
      unableCreateVisaApplication: "មិនអាចបង្កើតពាក្យសុំទិដ្ឋាការបានទេ",
      unableCreateCustomer: "មិនអាចបង្កើតអតិថិជនបានទេ",
      unableReplaceDocument: "មិនអាចជំនួសឯកសារបានទេ",
      unableRemoveDocument: "មិនអាចលុបឯកសារបានទេ",
      multipleFilesAllowed: "អនុញ្ញាតឯកសារច្រើន",
      oneFile: "ឯកសារមួយ",
      validPassportPlaceholder: "ឧ. លិខិតឆ្លងដែនមានសុពលភាព",
      exampleKhm: "ឧ. KHM",
      visaClass: "ថ្នាក់ទិដ្ឋាការ",
      effectiveDate: "ថ្ងៃចាប់ផ្តើមមានប្រសិទ្ធភាព",
      officialSourceUrl: "តំណប្រភពផ្លូវការ",
      selectValidDetails: "ជ្រើសរើសព័ត៌មានត្រឹមត្រូវដើម្បីបង្កើតកូដ",
      uniqueCodeChecked: "ពិនិត្យភាពតែមួយគត់នៃកូដពេលរក្សាទុក",
      entryLabel: "ចូល",
      actionsFor: "សកម្មភាពសម្រាប់",
      viewRequirementHistory: "មើលប្រវត្តិរបស់",
      visaRequirementsCreated: "បានបង្កើតតម្រូវការទិដ្ឋាការ។",
      unableLoadVisaRequirements: "មិនអាចផ្ទុកតម្រូវការទិដ្ឋាការបានទេ។",
      unableDuplicateVisa: "មិនអាចចម្លងកូដទិដ្ឋាការបានទេ។",
      visaStatusUpdated: "បានកែប្រែស្ថានភាពទិដ្ឋាការ។",
      unableUpdateVisaStatus: "មិនអាចកែប្រែស្ថានភាពទិដ្ឋាការបានទេ។",
      visaCodeDeleted: "បានលុបកូដទិដ្ឋាការ។",
      unableDeleteVisa: "មិនអាចលុបកូដទិដ្ឋាការបានទេ។",
      unableLoadHistory: "មិនអាចផ្ទុកប្រវត្តិតម្រូវការបានទេ។",
      unableRetry: "សូមព្យាយាមម្តងទៀត។",
      tourist: "ទេសចរណ៍",
      business: "ជំនួញ",
      student: "សិក្សា",
      work: "ការងារ",
      transit: "ឆ្លងកាត់",
      family: "គ្រួសារ",
      single: "ចូលម្តង",
      double: "ចូលពីរដង",
      multiple: "ចូលច្រើនដង",
      "30Days": "៣០ ថ្ងៃ",
      "60Days": "៦០ ថ្ងៃ",
      "90Days": "៩០ ថ្ងៃ",
      "180Days": "១៨០ ថ្ងៃ",
      "1Year": "១ ឆ្នាំ",
      "2Years": "២ ឆ្នាំ",
      fileRequired: "ត្រូវការឯកសារ",
      countCharacters: "តួអក្សរ",
      customer: "អតិថិជន",
      pending: "កំពុងរង់ចាំ",
      rejected: "បានបដិសេធ",
      verified: "បានផ្ទៀងផ្ទាត់",
      draft: "សេចក្តីព្រាង",
      underVerification: "កំពុងផ្ទៀងផ្ទាត់",
      readyToUpload: "រួចរាល់សម្រាប់ផ្ទុកឡើង",
      uploadFailedStatus: "ការផ្ទុកឡើងបានបរាជ័យ",
      reviewActionCreate: "បានបង្កើត",
      reviewActionUpdate: "បានកែប្រែ",
      reviewActionDelete: "បានលុប",
      invalidAcceptedFormats:
        "“{{name}}” ត្រូវប្រើទម្រង់ PDF, JPG, JPEG ឬ PNG។",
      activateVisaBeforeEdit:
        "សូមធ្វើឲ្យកូដទិដ្ឋាការនេះសកម្ម មុនពេលកែសម្រួលតម្រូវការ។",
      validCountryAndIso:
        "សូមបញ្ចូលឈ្មោះប្រទេស និងកូដ ISO ពីរតួអក្សរដែលត្រឹមត្រូវ។",
      chooseCountryBeforeSave: "សូមជ្រើសរើសប្រទេសមុនពេលរក្សាទុក។",
      checkVisaDetails: "សូមពិនិត្យរយៈពេល រយៈពេលស្នាក់នៅ ថ្នាក់ និងកូដសញ្ជាតិ។",
      completeEachRequirement:
        "សូមបំពេញតម្រូវការនីមួយៗ ដោយកំណត់ពិន្ទុជាចំនួនវិជ្ជមាន ឬសូន្យ និងលំដាប់បង្ហាញជាចំនួនគត់។",
      addAtLeastOneRequirement: "សូមបន្ថែមតម្រូវការឯកសារយ៉ាងហោចណាស់មួយ។",
      requirementNamesUnique: "ឈ្មោះតម្រូវការឯកសារនីមួយៗត្រូវតែខុសគ្នា។",
      chooseActiveCountry:
        "សូមជ្រើសរើសប្រទេសសកម្មដែលត្រឹមត្រូវ មុនពេលរក្សាទុក។",
      partialRequirementsUpdate:
        "បានរក្សាទុកកូដទិដ្ឋាការ {{code}} ប៉ុន្តែបានកែប្រែតម្រូវការមិនទាន់ពេញលេញ។ {{error}}",
      reviewRequirementsRetry: "សូមពិនិត្យតម្រូវការ ហើយព្យាយាមម្តងទៀត។",
      countrySavedVisaFailed:
        "បានរក្សាទុកប្រទេស {{country}} ប៉ុន្តែមិនអាចបញ្ចប់កូដទិដ្ឋាការ {{code}} បានទេ។ សូមផ្ទុកទំព័រឡើងវិញដើម្បីព្យាយាមម្តងទៀត។ {{error}}",
      countrySavedFormFailed:
        "បានរក្សាទុកប្រទេស {{country}} ប៉ុន្តែមិនអាចបញ្ចប់ទម្រង់ទិដ្ឋាការបានទេ។ {{error}}",
      pleaseRetry: "សូមព្យាយាមម្តងទៀត។",
      unableSaveVisaRequirements: "មិនអាចរក្សាទុកតម្រូវការទិដ្ឋាការបានទេ។",
      createdVisaCode: "បានបង្កើត {{code}}។",
      travelStartDate: "ថ្ងៃចាប់ផ្តើមធ្វើដំណើរ",
      returnDate: "ថ្ងៃត្រឡប់",
      japanTravelDatesRequired:
        "ត្រូវបញ្ចូលថ្ងៃចាប់ផ្តើម និងថ្ងៃត្រឡប់សម្រាប់ទិដ្ឋាការជប៉ុននេះ។",
      dateComponentsOutOfRange:
        "ថ្ងៃត្រូវនៅចន្លោះ ០១–៣១ ហើយខែត្រូវនៅចន្លោះ ០១–១២។",
      invalidCalendarDate: "សូមបញ្ចូលកាលបរិច្ឆេទត្រឹមត្រូវ។",
      returnDateAfterDeparture: "ថ្ងៃត្រឡប់ត្រូវនៅក្រោយថ្ងៃចាប់ផ្តើមធ្វើដំណើរ។",
      ocrCandidateReview:
        "ព័ត៌មានដែល OCR អានបានគឺជាព័ត៌មានសម្រាប់ផ្ទៀងផ្ទាត់។ សូមបញ្ជាក់ ឬកែតម្រូវ ព្រោះ OCR មិនអាចបញ្ជាក់ភាពត្រឹមត្រូវពិតប្រាកដនៃឯកសារបានទេ។",
      ocrExtracted: "OCR បានអានអត្ថបទ (ទំនុកចិត្ត {{confidence}}%)។",
      ocrManualReview: "លទ្ធផល OCR ត្រូវការការត្រួតពិនិត្យស្វ័យប្រវត្តិបន្ថែម។",
      passportDateOfBirth: "ថ្ងៃខែឆ្នាំកំណើតលើលិខិតឆ្លងដែន",
      passportExpiryDate: "ថ្ងៃផុតកំណត់លិខិតឆ្លងដែន",
      statementStartDate: "ថ្ងៃចាប់ផ្តើមរយៈពេលរបាយការណ៍",
      statementEndDate: "ថ្ងៃបញ្ចប់រយៈពេលរបាយការណ៍",
      closingBalanceUsd: "សមតុល្យបិទបញ្ជី",
      currency: "រូបិយប័ណ្ណ (ឧ. USD)",
      depositAmount: "ចំនួនប្រាក់បញ្ញើ",
      maturityDate: "ថ្ងៃផុតកំណត់ប្រាក់បញ្ញើ",
      saveExtractedValues: "រក្សាទុកព័ត៌មានឯកសារ",
      unableSaveExtractedValues: "មិនអាចរក្សាទុកព័ត៌មានឯកសារបានទេ",
      passportDatesRule:
        "ថ្ងៃកំណើតលើលិខិតឆ្លងដែនត្រូវតែត្រូវគ្នានឹងព័ត៌មានអ្នកដាក់ពាក្យ ហើយលិខិតឆ្លងដែនត្រូវមានសុពលភាពយ៉ាងតិច ៦ ខែបន្ទាប់ពីថ្ងៃត្រឡប់។",
      bankStatementRule:
        "របាយការណ៍ត្រូវគ្របដណ្តប់យ៉ាងតិច ៣ ខែ និងបង្ហាញសមតុល្យបិទបញ្ជី USD យ៉ាងតិច ៤,០០០ ដុល្លារ។",
      conditionalRequirement: "មានលក្ខខណ្ឌ",
      manualReview: "ត្រូវការត្រួតពិនិត្យដោយមនុស្ស",
    },
  },
};

i18n.use(initReactI18next).init({
  resources,
  lng: localStorage.getItem("lang") || "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

const sidebarItems = [
  { key: "dashboard", labelKey: "dashboard", icon: LayoutDashboard },
  { key: "applications", labelKey: "applications", icon: BriefcaseBusiness },
  { key: "requirements", labelKey: "requirements", icon: FolderCog },
  { key: "reports", labelKey: "reports", icon: FileText },
  { key: "users", labelKey: "users", icon: UserRound },
  { key: "settings", labelKey: "settings", icon: Settings },
];

function AppShell({ onLogout, profile = {} }) {
  const { t, i18n: i18nInstance } = useTranslation();
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth >= 1024);
  const [darkMode, setDarkMode] = useState(false);
  const isAdmin = String(profile.role || "").toUpperCase() === "ADMIN";

  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth >= 1024) setSidebarOpen(true);
      else if (window.innerWidth < 768) setSidebarOpen(false);
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const handleLanguageToggle = () => {
    const nextLang = i18nInstance.language === "en" ? "kh" : "en";
    i18nInstance.changeLanguage(nextLang);
    localStorage.setItem("lang", nextLang);
  };

  return (
    <div className={`workspace-ui ${darkMode ? "theme-dark" : "theme-light"}`}>
      <div className="h-screen overflow-hidden bg-slate-100 text-slate-900">
        <div className="flex h-full min-h-0 overflow-hidden">
          {sidebarOpen && (
            <button
              type="button"
              aria-label="Close navigation"
              className="fixed inset-0 z-30 bg-slate-950/45 lg:hidden"
              onClick={() => setSidebarOpen(false)}
            />
          )}
          <aside data-workspace-nav
            className={`${sidebarOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0 lg:w-24"} fixed inset-y-0 left-0 z-40 w-[min(86vw,18rem)] shrink-0 border-r border-slate-200 bg-white/95 shadow-xl backdrop-blur transition-all duration-200 lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:w-72 lg:translate-x-0 lg:self-start lg:shadow-sm`}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-4">
              <div className="flex items-center gap-3 overflow-hidden">
                <img
                  src="/global-eagle-logo.jpg"
                  alt="Global Eagle"
                  className="h-10 w-10 shrink-0 rounded-lg bg-black object-contain"
                />
                {sidebarOpen && (
                  <div className="font-semibold text-slate-800">
                    {t("appName")}
                  </div>
                )}
              </div>
              <button
                aria-label={sidebarOpen ? "Close navigation" : "Open navigation"}
                className="rounded-md p-2 text-slate-500 hover:bg-slate-100 lg:hidden"
                onClick={() => setSidebarOpen((prev) => !prev)}
              >
                <X className="h-5 w-5" />
              </button>
              <button
                aria-label="Toggle compact navigation"
                className="hidden rounded-md p-2 text-slate-500 hover:bg-slate-100 lg:block"
                onClick={() => setSidebarOpen((prev) => !prev)}
              >
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${sidebarOpen ? "rotate-0" : "rotate-180"}`}
                />
              </button>
            </div>

            <nav className="space-y-1 px-3 py-4">
              {sidebarItems
                .filter(({ key }) => key !== "users" || isAdmin)
                .map(({ key, labelKey, icon: Icon }) => (
                  <NavLink
                    key={key}
                    to={`/${key === "dashboard" ? "" : key}`}
                    end={key === "dashboard"}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${isActive ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"} ${!sidebarOpen ? "justify-center px-0" : ""}`
                    }
                    onClick={() => {
                      if (window.innerWidth < 1024) setSidebarOpen(false);
                    }}
                  >
                    <Icon className="h-5 w-5" />
                    {sidebarOpen && t(labelKey)}
                  </NavLink>
                ))}
            </nav>
          </aside>

          <main className="h-full min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain">
            <header className="workspace-toolbar">
              <div className="flex items-center gap-3">
                <button type="button" aria-label="Open navigation" className="lg:hidden" onClick={() => setSidebarOpen(true)}><Menu size={20}/></button>
                <span className="workspace-name">Global Eagle <span>Agency workspace</span></span>
              </div>
              <div className="workspace-account">
                <button type="button" onClick={handleLanguageToggle} aria-label={t("language")}>{i18nInstance.language === "kh" ? "ខ្មែរ" : "EN"}</button>
                <button type="button" onClick={() => setDarkMode(previous => !previous)} aria-label={darkMode ? t("switchToLight") : t("switchToDark")}>{darkMode ? "Light" : "Dark"}</button>
                <span className="workspace-user">{profile.name || t("account")}</span>
                <button type="button" onClick={onLogout}>{t("logout")}</button>
              </div>
            </header>

            <div className="workspace-content min-h-full min-w-0">
              <Routes>
                <Route path="/" element={<DashboardPage profile={profile} />} />
                <Route
                  path="/applications/:applicationId/checklist"
                  element={<ApplicationsPage profile={profile} />}
                />
                <Route
                  path="/applications"
                  element={<ApplicationsPage profile={profile} />}
                />
                <Route
                  path="/applications/:applicationId/results"
                  element={<VerificationResultsPage profile={profile} />}
                />
                <Route path="/verification" element={<VerificationPage />} />
                <Route
                  path="/bank-statement-analysis"
                  element={<Navigate to="/applications" replace />}
                />
                <Route path="/requirements" element={<RequirementsPage />} />
                <Route path="/reports" element={<ReportsPage />} />
                <Route
                  path="/users"
                  element={
                    isAdmin ? (
                      <UsersPage profile={profile} />
                    ) : (
                      <Navigate to="/" replace />
                    )
                  }
                />
                <Route
                  path="/settings"
                  element={<SettingsPage profile={profile} />}
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </div>
          </main>
        </div>
      </div>
    </div>
  );
}

function DocumentsPage() {
  const { t } = useTranslation();
  const { data: documents = [] } = useSecureData(async () => {
    const response = await api.get("/documents");
    return response.data.documents || [];
  }, [t]);
  const [applications, setApplications] = useState([]);
  const [requirements, setRequirements] = useState([]);
  const [applicationId, setApplicationId] = useState("");
  const [requirementId, setRequirementId] = useState("");
  const [uploadedDocuments, setUploadedDocuments] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadError, setUploadError] = useState("");
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    Promise.all([api.get("/visa-types/applications"), api.get("/requirements")])
      .then(([applicationsResponse, requirementsResponse]) => {
        const items = applicationsResponse.data.applications || [];
        const requirementItems = requirementsResponse.data.requirements || [];
        setApplications(items);
        setRequirements(requirementItems);
        setApplicationId(items[0]?._id || "");
        const selectedVisaTypeId =
          items[0]?.visaTypeId || items[0]?.visaType || "";
        setRequirementId(
          requirementItems.find(
            (item) => (item.visaTypeId || item.visaType) === selectedVisaTypeId,
          )?._id || "",
        );
      })
      .catch((requestError) =>
        setUploadError(
          requestError?.response?.data?.message || t("unableLoadChecklist"),
        ),
      );
  }, [t]);

  const selectedApplication = applications.find(
    (item) => item._id === applicationId,
  );
  const applicableRequirements = requirements.filter((item) => {
    const itemVisaTypeId = item.visaTypeId || item.visaType;
    const applicationVisaTypeId =
      selectedApplication?.visaTypeId || selectedApplication?.visaType;
    return !itemVisaTypeId || itemVisaTypeId === applicationVisaTypeId;
  });

  const handleUpload = async () => {
    if (!selectedFile || !applicationId) return;
    if (selectedFile.size > 10 * 1024 * 1024) {
      setUploadError(`${t("fileExceedsLimit")} 10 ${t("mb")}`);
      return;
    }
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("applicationId", applicationId);
    formData.append("requirementId", requirementId);
    setUploading(true);
    setUploadError("");
    try {
      const response = await api.post("/documents/upload", formData);
      setUploadedDocuments((current) => [response.data.document, ...current]);
      setSelectedFile(null);
    } catch (requestError) {
      setUploadError(
        requestError?.response?.data?.message || t("uploadFailed"),
      );
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm uppercase tracking-[0.14em] text-blue-600">
            {t("documents")}
          </p>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">
            {t("documents")}
          </h1>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-8 text-center">
            <UploadCloud className="mx-auto mb-3 h-12 w-12 text-blue-600" />
            <div className="font-medium text-slate-800">
              {t("dragDropFiles")}
            </div>
            <div className="mt-2 text-sm text-slate-500">
              PDF, JPG, PNG {t("upTo")} 10 {t("mb")}
            </div>
            <select
              aria-label={t("applicationForDocument")}
              className="mt-4 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
              value={applicationId}
              onChange={(event) => {
                const nextId = event.target.value;
                const nextApplication = applications.find(
                  (application) => application._id === nextId,
                );
                setApplicationId(nextId);
                setRequirementId(
                  requirements.find(
                    (item) => item.visaType === nextApplication?.visaType,
                  )?._id || "",
                );
              }}
            >
              <option value="">{t("selectApplication")}</option>
              {applications.map((application) => (
                <option key={application._id} value={application._id}>
                  {application.applicationNumber}
                </option>
              ))}
            </select>
            <select
              required
              aria-label={t("documentRequirement")}
              className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
              value={requirementId}
              onChange={(event) => setRequirementId(event.target.value)}
            >
              <option value="">{t("selectRequirement")}</option>
              {applicableRequirements.map((requirement) => (
                <option key={requirement._id} value={requirement._id}>
                  {requirement.name}
                  {requirement.required
                    ? ` · ${t("required")}`
                    : ` · ${t("conditionalOptional")}`}
                </option>
              ))}
            </select>
            <input
              accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              type="file"
              className="mt-3 block w-full text-sm text-slate-500"
              onChange={(event) => setSelectedFile(event.target.files?.[0])}
            />
            <button
              type="button"
              disabled={!selectedFile || !applicationId || uploading}
              className="mt-4 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              onClick={handleUpload}
            >
              {uploading ? t("uploading") : t("uploadDocument")}
            </button>
            {uploadError && (
              <div role="alert" className="mt-3 text-sm text-red-600">
                {uploadError}
              </div>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <h3 className="mb-4 text-lg font-semibold text-slate-900">
            {t("documentChecklist")}
          </h3>
          <div className="space-y-3">
            {[...uploadedDocuments, ...documents].filter(
              (doc) => doc.applicationId === applicationId,
            ).length === 0 ? (
              <div className="text-slate-500">{t("noData")}</div>
            ) : (
              [...uploadedDocuments, ...documents]
                .filter((doc) => doc.applicationId === applicationId)
                .map((doc) => (
                  <div
                    key={doc._id}
                    className="flex items-center justify-between rounded-xl border border-slate-200 px-4 py-3"
                  >
                    <div>
                      <div className="font-medium text-slate-800">
                        {doc.fileName || "document.pdf"}
                      </div>
                      <div className="text-sm text-slate-500">
                        {doc.status || "PENDING"}
                      </div>
                    </div>
                    <div className="text-sm text-slate-500">
                      {doc.mimeType || "application/pdf"}
                    </div>
                  </div>
                ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function VerificationPage() {
  const { t } = useTranslation();
  const [applications, setApplications] = useState([]);
  const [applicationId, setApplicationId] = useState("");
  const [history, setHistory] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [documentsLoading, setDocumentsLoading] = useState(false);
  const [naReasons, setNaReasons] = useState({});
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [retryingDocumentId, setRetryingDocumentId] = useState("");
  const verificationInFlight = useRef(false);

  const currentDocuments = documents.filter(
    (document) =>
      !["DELETED", "REPLACED"].includes(
        String(document.status || "").toUpperCase(),
      ),
  );
  const documentsNeedingOcr = currentDocuments.filter(
    (document) =>
      !String(document.requirementCode || "").toLowerCase().includes("photo") &&
      String(document.ocrStatus || "").toUpperCase() !== "NOT_REQUIRED" &&
      String(document.ocrStatus || "").toUpperCase() !== "OCR_COMPLETED",
  );
  const pendingOcrDocumentIds = currentDocuments
    .filter((document) =>
      ["OCR_PENDING", "OCR_PROCESSING"].includes(
        String(document.ocrStatus || "").toUpperCase(),
      ),
    )
    .map((document) => document._id)
    .join(",");

  useEffect(() => {
    Promise.all([api.get("/visa-types/applications"), api.get("/verification")])
      .then(([applicationsResponse, historyResponse]) => {
        const items = applicationsResponse.data.applications || [];
        setApplications(items);
        setApplicationId(items[0]?._id || "");
        setHistory(historyResponse.data.verifications || []);
      })
      .catch((requestError) =>
        setError(
          requestError?.response?.data?.message ||
            "Unable to load verification data",
        ),
      );
  }, []);

  useEffect(() => {
    if (!applicationId) {
      setDocuments([]);
      setDocumentsLoading(false);
      return undefined;
    }
    let active = true;
    setDocumentsLoading(true);
    api
      .get("/documents", { params: { applicationId } })
      .then((response) => {
        if (active) setDocuments(response.data.documents || []);
      })
      .catch((requestError) => {
        if (active) {
          setError(
            requestError?.response?.data?.message ||
              "Unable to load document processing status",
          );
        }
      })
      .finally(() => {
        if (active) setDocumentsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [applicationId]);

  useEffect(() => {
    if (!pendingOcrDocumentIds) return undefined;
    const active = { current: true };
    const timer = { current: undefined };
    const pollProcessing = async () => {
      const results = await Promise.all(
        pendingOcrDocumentIds.split(",").map(async (documentId) => {
          try {
            const response = await api.get(
              `/documents/${documentId}/processing`,
            );
            return { documentId, progress: response.data };
          } catch (requestError) {
            return { documentId, error: requestError };
          }
        }),
      );
      if (!active.current) return;
      const failedRequest = results.find((result) => result.error);
      if (failedRequest) {
        setError(
          failedRequest.error?.response?.data?.message ||
            failedRequest.error?.message ||
            "Unable to load document processing status",
        );
      }
      setDocuments((current) =>
        current.map((document) => {
          const result = results.find(
            (item) => item.documentId === document._id,
          );
          if (!result?.progress) return document;
          return {
            ...document,
            workflowStatus: result.progress.workflowStatus,
            ocrStatus: result.progress.ocrStatus,
            ocrPagesProcessed: result.progress.pagesProcessed,
            ocrTotalPages: result.progress.totalPages,
            ocrProgressPercent: result.progress.progressPercent,
            ocrMessage: result.progress.message,
            ocrError: result.progress.error,
          };
        }),
      );
      timer.current = window.setTimeout(pollProcessing, 750);
    };
    void pollProcessing();
    return () => {
      active.current = false;
      window.clearTimeout(timer.current);
    };
  }, [pendingOcrDocumentIds]);

  const selectedHistory = history
    .filter((item) => item.applicationId === applicationId)
    .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const latest = selectedHistory[0];
  const retryDocumentOcr = async (document) => {
    setRetryingDocumentId(document._id);
    setError("");
    try {
      const response = await api.post(`/documents/${document._id}/ocr/retry`);
      setDocuments((current) =>
        current.map((item) =>
          item._id === document._id
            ? { ...item, ...response.data.document }
            : item,
        ),
      );
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          requestError?.message ||
          t("documentReadingFailed"),
      );
    } finally {
      setRetryingDocumentId("");
    }
  };
  const runVerification = async () => {
    if (
      !applicationId ||
      verificationInFlight.current ||
      documentsLoading ||
      documentsNeedingOcr.length
    ) {
      return;
    }
    verificationInFlight.current = true;
    setIsRunning(true);
    setError("");
    try {
      const notApplicableRequirements = Object.fromEntries(
        Object.entries(naReasons).filter(([, reason]) => reason.trim()),
      );
      const response = await api.post("/verification/run", {
        applicationId,
        notApplicableRequirements,
      });
      setHistory((current) => [response.data.verification, ...current]);
    } catch (requestError) {
      setError(
        requestError?.response?.data?.message ||
          "Unable to run document analysis",
      );
    } finally {
      verificationInFlight.current = false;
      setIsRunning(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm uppercase tracking-[0.14em] text-blue-600">
            {t("verification")}
          </p>
          <h1 className="mt-2 text-3xl font-bold text-slate-900">
            Visa Readiness Analysis
          </h1>
        </div>
        <select
          aria-label="Select application"
          className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
          value={applicationId}
          onChange={(event) => setApplicationId(event.target.value)}
        >
          <option value="">Select application</option>
          {applications.map((application) => (
            <option key={application._id} value={application._id}>
              {application.applicationNumber} · {application.destination}
            </option>
          ))}
        </select>
      </div>

      <p className="text-sm text-slate-500">
        This score measures document readiness only. It does not predict or
        guarantee a visa decision.
      </p>
      {error && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
        >
          {error}
        </div>
      )}
      {documentsLoading && (
        <p
          role="status"
          className="rounded-xl bg-slate-50 p-3 text-sm text-slate-600"
        >
          {t("loadingChecklist")}
        </p>
      )}
      {documentsNeedingOcr.length > 0 && (
        <section className="space-y-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
          <p role="status" className="text-sm font-medium text-amber-900">
            {t("ocrVerificationBlocked")}
          </p>
          {documentsNeedingOcr.map((document) => {
            const status = String(document.ocrStatus || "").toUpperCase();
            const percent = Math.min(
              100,
              Math.max(0, Number(document.ocrProgressPercent) || 0),
            );
            return (
              <div
                key={document._id}
                className="rounded-lg border border-amber-200 bg-white p-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-slate-800">
                    {document.fileName || t("uploadedDocuments")}
                  </span>
                  <span className="text-sm text-slate-700">
                    {status === "OCR_FAILED"
                      ? document.ocrError || t("documentReadingFailed")
                      : status === "OCR_PARTIAL"
                        ? document.ocrError ||
                          document.ocrMessage ||
                          t("documentReadingFailed")
                        : status === "OCR_COMPLETED"
                          ? t("ocrCompletedReady")
                          : t("readingDocument")}
                  </span>
                </div>
                {Number(document.ocrTotalPages) > 0 && (
                  <>
                    <p className="mt-1 text-xs text-slate-600">
                      {t("ocrPagesProgress", {
                        processed: document.ocrPagesProcessed || 0,
                        total: document.ocrTotalPages,
                      })}
                      {" · "}
                      {t("ocrProgressPercent", { percent })}
                    </p>
                    <div
                      className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100"
                      role="progressbar"
                      aria-valuenow={percent}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={t("readingDocument")}
                    >
                      <div
                        className="h-full rounded-full bg-blue-600 transition-all"
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  </>
                )}
                {["OCR_FAILED", "OCR_PARTIAL", "UPLOADED"].includes(status) && (
                  <button
                    type="button"
                    disabled={retryingDocumentId === document._id}
                    onClick={() => retryDocumentOcr(document)}
                    className="mt-2 text-sm font-semibold text-blue-700 underline disabled:opacity-50"
                  >
                    {retryingDocumentId === document._id
                      ? t("readingDocument")
                      : t("retryOcr")}
                  </button>
                )}
              </div>
            );
          })}
        </section>
      )}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-3">
            <div className="rounded-xl bg-blue-50 p-3 text-blue-600">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div>
              <div className="text-lg font-semibold text-slate-900">
                Document Readiness Score
              </div>
              <div className="text-sm text-slate-500">
                {latest
                  ? `${latest.completenessScore}% · ${latest.status}`
                  : "Run an analysis to calculate readiness"}
              </div>
            </div>
          </div>
          <div className="rounded-xl bg-slate-50 p-4">
            {!latest?.results?.length && (
              <div className="text-sm text-slate-500">
                No analysis results for this application yet.
              </div>
            )}
            {latest?.results?.map((item) => (
              <div
                key={item.requirementId}
                className="border-b border-slate-200 py-2 text-sm last:border-0"
              >
                <div className="flex items-center justify-between">
                  <span className="text-slate-700">{item.requirementName}</span>
                  <span className="font-medium text-slate-600">
                    {item.status}
                  </span>
                </div>
                {item.conditional && item.status !== "NOT_APPLICABLE" && (
                  <input
                    aria-label={`Reason ${item.requirementName} is not applicable`}
                    className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
                    placeholder="Justification if not applicable"
                    value={naReasons[item.requirementId] || ""}
                    onChange={(event) =>
                      setNaReasons((current) => ({
                        ...current,
                        [item.requirementId]: event.target.value,
                      }))
                    }
                  />
                )}
                {item.status === "NOT_APPLICABLE" && (
                  <div className="mt-1 text-xs text-slate-500">
                    {item.explanationEn}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-col gap-3">
            <button
              disabled={
                !applicationId ||
                isRunning ||
                documentsLoading ||
                documentsNeedingOcr.length > 0
              }
              onClick={runVerification}
              className="rounded-xl bg-blue-600 px-4 py-3 font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isRunning ? "Analyzing…" : t("requestAiCheck")}
            </button>
            <button
              disabled={
                !applicationId ||
                isRunning ||
                documentsLoading ||
                documentsNeedingOcr.length > 0
              }
              onClick={runVerification}
              className="rounded-xl border border-slate-200 px-4 py-3 font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("recheckDocuments")}
            </button>
            <button
              disabled
              title="PDF report generation is not available yet"
              className="rounded-xl border border-slate-200 px-4 py-3 font-medium text-slate-400 disabled:cursor-not-allowed"
            >
              {t("exportPdf")} · Unavailable
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="mb-4 text-lg font-semibold text-slate-900">
          Recommendations
        </h3>
        {!latest?.results?.length ? (
          <div className="text-slate-500">{t("noData")}</div>
        ) : (
          latest.results
            .filter((item) => item.status !== "PASS")
            .map((item) => (
              <div
                key={item.requirementId}
                className="mb-3 rounded-xl border border-slate-200 p-4"
              >
                <div className="flex items-center justify-between">
                  <div className="font-medium text-slate-800">
                    {item.requirementName} · {item.status}
                  </div>
                </div>
                <p className="mt-2 text-sm text-slate-600">
                  {item.suggestedCorrection}
                </p>
              </div>
            ))
        )}
      </div>
    </div>
  );
}

function App() {
  const [auth, setAuth] = useState({
    status: "checking",
    profile: null,
    error: "",
  });
  const [setupRequired, setSetupRequired] = useState(false);
  useEffect(() => { api.get("/setup/status").then(r => setSetupRequired(Boolean(r.data.requiresSetup))).catch(() => {}); }, []);
  const initialRestoreStarted = useRef(false);
  const sessionToken = useRef(getStoredAuthToken());

  const restoreSession = useCallback(async () => {
    const token = getStoredAuthToken();
    if (!token) {
      sessionToken.current = "";
      setAuth({ status: "signed-out", profile: null, error: "" });
      return;
    }

    setAuth({ status: "checking", profile: null, error: "" });
    try {
      const response = await api.get("/auth/me");
      sessionToken.current = token;
      setAuth({
        status: "authenticated",
        profile: response.data.user || {},
        error: "",
      });
    } catch (error) {
      const status = error?.response?.status;
      if (status === 401 || status === 404) {
        localStorage.removeItem("visadoc_token");
        localStorage.removeItem("access_token");
        sessionToken.current = "";
        setAuth({ status: "signed-out", profile: null, error: "" });
        return;
      }
      setAuth({
        status: "error",
        profile: null,
        error:
          error?.response?.data?.message ||
          "Unable to verify your saved session.",
      });
    }
  }, []);

  useEffect(() => {
    if (!initialRestoreStarted.current) {
      initialRestoreStarted.current = true;
      void restoreSession();
    }
    const syncSession = () => {
      const token = getStoredAuthToken();
      if (token === sessionToken.current) {
        return;
      }
      sessionToken.current = token;
      if (token) {
        void restoreSession();
      } else {
        setAuth({ status: "signed-out", profile: null, error: "" });
      }
    };
    const expireSession = () => {
      sessionToken.current = "";
      setAuth({ status: "signed-out", profile: null, error: "" });
    };
    window.addEventListener("storage", syncSession);
    window.addEventListener("visadoc:auth-expired", expireSession);
    return () => {
      window.removeEventListener("storage", syncSession);
      window.removeEventListener("visadoc:auth-expired", expireSession);
    };
  }, [restoreSession]);

  const handleLogout = () => {
    localStorage.removeItem("visadoc_token");
    localStorage.removeItem("access_token");
    sessionStorage.removeItem("visadoc_token");
    sessionStorage.removeItem("access_token");
    sessionToken.current = "";
    setAuth({ status: "signed-out", profile: null, error: "" });
  };

  const handleLogin = (profile) => {
    sessionToken.current = getStoredAuthToken();
    setAuth({ status: "authenticated", profile, error: "" });
  };

  return (
    <BrowserRouter>
      {auth.status === "checking" ? (
        <div className="flex min-h-screen items-center justify-center text-sm text-slate-600">
          Checking your session…
        </div>
      ) : auth.status === "error" ? (
        <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-6 text-center">
          <p role="alert" className="text-sm text-slate-700">
            {auth.error}
          </p>
          <button
            type="button"
            onClick={() => void restoreSession()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Retry
          </button>
        </div>
      ) : auth.status === "signed-out" ? (
        setupRequired ? <FirstRunSetup onComplete={profile => { setSetupRequired(false); handleLogin(profile); }} /> : <LoginPage onLogin={handleLogin} />
      ) : (
        <AppShell profile={auth.profile} onLogout={handleLogout} />
      )}
    </BrowserRouter>
  );
}

export default App;
