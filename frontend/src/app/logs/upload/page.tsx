"use client";

import React, { useState, useRef } from "react";
import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { API_ENDPOINTS } from "@/config/api";
import {
  uploadLogFile,
  indexLogJob,
  ApiError,
  type LogUploadResponse,
  type LogIndexingResponse,
} from "@/lib/api/logs";

const ALLOWED_EXTENSIONS = [".csv", ".json", ".log", ".txt", ".syslog"];
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB

type IngestionPhase = "idle" | "uploading" | "indexing" | "completed" | "error";

interface IngestionSuccess {
  upload: LogUploadResponse;
  indexing: LogIndexingResponse;
}

export default function LogUploadPage() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [serviceName, setServiceName] = useState("");
  const [environment, setEnvironment] = useState("development");

  // Ingestion lifecycle states
  const [phase, setPhase] = useState<IngestionPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [isServiceUnavailable, setIsServiceUnavailable] = useState(false);
  const [successResult, setSuccessResult] = useState<IngestionSuccess | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const validateAndSetFile = (file: File) => {
    setError(null);
    setIsServiceUnavailable(false);

    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError(
        `Unsupported file format '${ext}'. Supported extensions are: ${ALLOWED_EXTENSIONS.join(
          ", "
        )}.`
      );
      setSelectedFile(null);
      return;
    }

    if (file.size === 0) {
      setError("Selected file is empty (0 bytes). Please choose a valid log file.");
      setSelectedFile(null);
      return;
    }

    if (file.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `File size (${formatFileSize(file.size)}) exceeds maximum allowed limit of 50 MB.`
      );
      setSelectedFile(null);
      return;
    }

    setSelectedFile(file);
    setSuccessResult(null);
    setPhase("idle");
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (phase === "uploading" || phase === "indexing") return;
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    if (phase === "uploading" || phase === "indexing") return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleStartIngestion = async () => {
    if (!selectedFile) return;

    setError(null);
    setIsServiceUnavailable(false);
    setSuccessResult(null);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      // Stage 1: Upload Job registration in PostgreSQL
      setPhase("uploading");
      const uploadResponse = await uploadLogFile(
        selectedFile,
        {
          service_name: serviceName.trim() || undefined,
          environment: environment || "development",
        },
        controller.signal
      );

      // Stage 2, 3, 4: Format Parsing, Normalization, OpenSearch Bulk Indexing
      setPhase("indexing");
      const indexingResponse = await indexLogJob(uploadResponse.job_id, controller.signal);

      setSuccessResult({
        upload: uploadResponse,
        indexing: indexingResponse,
      });
      setPhase("completed");
    } catch (err: unknown) {
      setPhase("error");
      if (err instanceof ApiError) {
        setIsServiceUnavailable(err.isServiceUnavailable);
        setError(err.detail || err.message);
      } else {
        const msg = err instanceof Error ? err.message : "Ingestion request failed";
        setError(msg);
      }
    } finally {
      abortControllerRef.current = null;
    }
  };

  const handleReset = () => {
    setSelectedFile(null);
    setSuccessResult(null);
    setError(null);
    setIsServiceUnavailable(false);
    setPhase("idle");
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const isBusy = phase === "uploading" || phase === "indexing";

  return (
    <div className="flex flex-col w-full p-4 sm:p-6 gap-6 max-w-7xl mx-auto">
      {/* Hidden native file input */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        accept=".csv,.json,.log,.txt,.syslog"
        className="hidden"
      />

      {/* Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-4 border-b border-[#EDEFF3]">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-[12px] text-[#606366]">
            <Link href="/logs" className="hover:text-[#1B1A1A] transition-colors">
              Logs
            </Link>
            <span>/</span>
            <span className="text-[#1B1A1A] font-medium">Upload</span>
          </div>
          <div className="flex items-center gap-3">
            <h1 className="text-[22px] font-semibold text-[#1B1A1A] tracking-tight">
              Ingest Log Records
            </h1>
            <StatusBadge
              label={isBusy ? "PROCESSING PIPELINE" : phase === "completed" ? "INDEXED" : "READY"}
              variant={phase === "completed" ? "healthy" : isBusy ? "info" : "neutral"}
              dot
            />
          </div>
        </div>

        <Link
          href="/logs"
          className="flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-white border border-[#EDEFF3] hover:bg-[#F5F6F9] text-[#1B1A1A] text-[13px] font-medium transition-colors shadow-xs w-fit shrink-0"
        >
          <Icon name="arrow_back" size={16} className="text-[#606366]" />
          <span>Back to Log Explorer</span>
        </Link>
      </div>

      {/* Error / 503 Alert Banner */}
      {error && (
        <div
          className={`p-4 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-xs ${
            isServiceUnavailable
              ? "bg-[#FEF7E6] border-[#FDE8B3]"
              : "bg-[#FEECEB] border-[#FCD3CE]"
          }`}
        >
          <div className="flex items-center gap-3">
            <div
              className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
                isServiceUnavailable
                  ? "bg-[#FDE8B3] text-[#B77900]"
                  : "bg-[#FCD3CE] text-[#E63316]"
              }`}
            >
              <Icon name={isServiceUnavailable ? "warning" : "error"} size={20} />
            </div>
            <div className="flex flex-col">
              <span
                className={`text-[13px] font-semibold ${
                  isServiceUnavailable ? "text-[#B77900]" : "text-[#E63316]"
                }`}
              >
                {isServiceUnavailable
                  ? "OpenSearch Cluster Unavailable (HTTP 503)"
                  : "Ingestion Pipeline Failed"}
              </span>
              <span className="text-[12px] text-[#606366]">{error}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {selectedFile && (
              <button
                type="button"
                onClick={handleStartIngestion}
                disabled={isBusy}
                className="px-3 py-1.5 rounded-lg text-[12px] font-medium text-white bg-[#4D80FC] hover:bg-[#396fe4] transition-colors cursor-pointer"
              >
                Retry Ingestion
              </button>
            )}
            <button
              type="button"
              onClick={() => setError(null)}
              className="px-2.5 py-1.5 rounded-lg text-[12px] font-medium text-[#606366] hover:bg-black/5 transition-colors cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Main Content Layout: Dominant Content on Left, Secondary Panel on Right */}
      <div className="upload-container-grid">
        {/* Dominant Left Column: Dropzone, Ingestion Controls, and Lifecycle */}
        <div className="flex flex-col gap-6 min-w-0 w-full">
          {/* Main Upload Dropzone */}
          <div
            onClick={() => {
              if (!isBusy) fileInputRef.current?.click();
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={`p-8 sm:p-10 rounded-xl border-2 border-dashed transition-all flex flex-col items-center justify-center text-center gap-4 shadow-xs w-full ${
              isBusy
                ? "bg-[#F9FAFC] border-[#D5D8DE] cursor-not-allowed opacity-80"
                : isDragOver
                ? "bg-[#EEF3FF] border-[#4D80FC] scale-[1.005]"
                : selectedFile
                ? "bg-white border-[#BFDBFE] hover:border-[#4D80FC] cursor-pointer"
                : "bg-white border-[#EDEFF3] hover:border-[#4D80FC] cursor-pointer"
            }`}
          >
            <div
              className={`w-14 h-14 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                isBusy
                  ? "bg-[#F5F6F9] text-[#9B9FA3]"
                  : selectedFile
                  ? "bg-[#EDF9ED] text-[#65C861] border border-[#C5ECC4]"
                  : "bg-[#EEF3FF] border border-[#BFDBFE] text-[#4D80FC]"
              }`}
            >
              {isBusy ? (
                <div className="w-6 h-6 border-2 border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
              ) : selectedFile ? (
                <Icon name="check_circle" size={28} />
              ) : (
                <Icon name="cloud_upload" size={28} />
              )}
            </div>

            <div className="flex flex-col gap-1 max-w-lg">
              {selectedFile ? (
                <>
                  <span className="text-[16px] font-semibold text-[#1B1A1A]">
                    {selectedFile.name}
                  </span>
                  <span className="text-[12px] font-mono text-[#606366]">
                    {formatFileSize(selectedFile.size)} • Click or drop another file to replace
                  </span>
                </>
              ) : (
                <>
                  <span className="text-[16px] sm:text-[17px] font-semibold text-[#1B1A1A]">
                    Drag and drop log files, or browse
                  </span>
                  <p className="text-[13px] text-[#606366] leading-relaxed">
                    Supported formats: CSV, JSON, Syslog (RFC 3164 / 5424), and standard application log files (.log, .txt)
                  </p>
                </>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-center gap-2 sm:gap-3 text-[11px] font-mono text-[#606366] pt-1">
              <span className="px-2.5 py-1 rounded bg-[#F5F6F9] border border-[#EDEFF3]">
                Max file size: 50MB
              </span>
              <span className="text-[#9B9FA3] hidden sm:inline">•</span>
              <span className="px-2.5 py-1 rounded bg-[#F5F6F9] border border-[#EDEFF3]">
                Automatic format detection
              </span>
              <span className="text-[#9B9FA3] hidden sm:inline">•</span>
              <span className="px-2.5 py-1 rounded bg-[#F5F6F9] border border-[#EDEFF3]">
                Monthly index partitioning
              </span>
            </div>
          </div>

          {/* Action & Metadata Controls Card (Visible when file is selected and not completed) */}
          {selectedFile && phase !== "completed" && (
            <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] shadow-xs flex flex-col gap-4 w-full">
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold text-[#1B1A1A]">
                  Ingestion Parameters
                </span>
                <span className="text-[11px] text-[#9B9FA3]">
                  Optional service & environment metadata
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12px]">
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-medium text-[#606366]">
                    Service Identifier (Optional)
                  </label>
                  <input
                    type="text"
                    disabled={isBusy}
                    value={serviceName}
                    onChange={(e) => setServiceName(e.target.value)}
                    placeholder="e.g. payment-service"
                    className="px-3 py-2 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] font-mono text-[12px] focus:outline-none focus:border-[#4D80FC] disabled:opacity-60"
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-medium text-[#606366]">
                    Environment
                  </label>
                  <select
                    disabled={isBusy}
                    value={environment}
                    onChange={(e) => setEnvironment(e.target.value)}
                    className="px-3 py-2 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] text-[12px] focus:outline-none focus:border-[#4D80FC] disabled:opacity-60 cursor-pointer"
                  >
                    <option value="development">development</option>
                    <option value="production">production</option>
                    <option value="staging">staging</option>
                  </select>
                </div>
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-[#EDEFF3]">
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={handleReset}
                  className="px-3 py-2 rounded-lg text-[12px] font-medium text-[#606366] hover:bg-[#F5F6F9] transition-colors cursor-pointer"
                >
                  Clear Selection
                </button>
                <button
                  type="button"
                  disabled={isBusy}
                  onClick={handleStartIngestion}
                  className="px-5 py-2.5 rounded-lg text-[13px] font-medium text-white bg-[#4D80FC] hover:bg-[#396fe4] transition-colors shadow-xs flex items-center gap-2 cursor-pointer disabled:opacity-60"
                >
                  {isBusy ? (
                    <>
                      <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                      <span>
                        {phase === "uploading"
                          ? "Uploading payload..."
                          : "Parsing & Indexing..."}
                      </span>
                    </>
                  ) : (
                    <>
                      <Icon name="play_arrow" size={16} />
                      <span>Ingest & Index Logs</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Success Ingestion Summary Card (Shown when pipeline completes) */}
          {phase === "completed" && successResult && (
            <div className="p-5 sm:p-6 rounded-xl bg-[#EDF9ED] border border-[#C5ECC4] shadow-xs flex flex-col gap-4 w-full">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-white border border-[#C5ECC4] flex items-center justify-center text-[#65C861] shrink-0 shadow-xs">
                    <Icon name="check" size={22} />
                  </div>
                  <div className="flex flex-col">
                    <h3 className="text-[15px] font-semibold text-[#1B1A1A]">
                      Log Ingestion Successfully Completed
                    </h3>
                    <p className="text-[12px] text-[#606366]">
                      {successResult.indexing.message || "All records parsed, normalized, and indexed into OpenSearch."}
                    </p>
                  </div>
                </div>
                <span className="font-mono text-[11px] font-bold text-[#65C861] bg-white px-2.5 py-1 rounded-md border border-[#C5ECC4]">
                  STATUS: {successResult.indexing.status}
                </span>
              </div>

              {/* Indexed Metrics Grid */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-[12px] pt-1">
                <div className="p-3 bg-white rounded-lg border border-[#C5ECC4] flex flex-col gap-0.5">
                  <span className="text-[10px] text-[#9B9FA3] uppercase font-semibold">Indexed</span>
                  <span className="text-[16px] font-bold font-mono text-[#1B1A1A]">
                    {successResult.indexing.indexed_records}
                  </span>
                </div>
                <div className="p-3 bg-white rounded-lg border border-[#C5ECC4] flex flex-col gap-0.5">
                  <span className="text-[10px] text-[#9B9FA3] uppercase font-semibold">Total Records</span>
                  <span className="text-[16px] font-bold font-mono text-[#1B1A1A]">
                    {successResult.indexing.total_records}
                  </span>
                </div>
                <div className="p-3 bg-white rounded-lg border border-[#C5ECC4] flex flex-col gap-0.5">
                  <span className="text-[10px] text-[#9B9FA3] uppercase font-semibold">Quarantined</span>
                  <span className="text-[16px] font-bold font-mono text-[#1B1A1A]">
                    {successResult.indexing.quarantined_records}
                  </span>
                </div>
                <div className="p-3 bg-white rounded-lg border border-[#C5ECC4] flex flex-col gap-0.5">
                  <span className="text-[10px] text-[#9B9FA3] uppercase font-semibold">Target Index</span>
                  <span className="text-[11px] font-medium font-mono text-[#4D80FC] truncate" title={successResult.indexing.target_index}>
                    {successResult.indexing.target_index}
                  </span>
                </div>
              </div>

              {/* Details & Action Bar */}
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2 border-t border-[#C5ECC4]/60">
                <div className="flex items-center gap-2 text-[11px] font-mono text-[#606366]">
                  <span>Job ID:</span>
                  <span className="text-[#1B1A1A] font-semibold">{successResult.upload.job_id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handleReset}
                    className="px-3 py-2 rounded-lg text-[12px] font-medium text-[#1B1A1A] bg-white border border-[#C5ECC4] hover:bg-[#F5F6F9] transition-colors cursor-pointer"
                  >
                    Upload Another File
                  </button>
                  <Link
                    href="/logs"
                    className="px-4 py-2 rounded-lg text-[12px] font-medium text-white bg-[#4D80FC] hover:bg-[#396fe4] transition-colors shadow-xs flex items-center gap-1.5"
                  >
                    <Icon name="search" size={15} />
                    <span>View in Log Explorer</span>
                  </Link>
                </div>
              </div>
            </div>
          )}

          {/* Ingestion Pipeline Lifecycle */}
          <div className="p-5 sm:p-6 rounded-xl bg-white border border-[#EDEFF3] shadow-xs flex flex-col gap-4 w-full">
            <div className="flex items-center justify-between">
              <h2 className="text-[14px] font-semibold text-[#1B1A1A]">
                Ingestion Pipeline Lifecycle (Backend Verified)
              </h2>
              <span
                className={`font-mono text-[11px] font-medium px-2 py-0.5 rounded border ${
                  phase === "completed"
                    ? "text-[#65C861] bg-[#EDF9ED] border-[#C5ECC4]"
                    : isBusy
                    ? "text-[#4D80FC] bg-[#EEF3FF] border-[#BFDBFE]"
                    : "text-[#65C861] bg-[#EDF9ED] border-[#C5ECC4]"
                }`}
              >
                {phase === "completed"
                  ? "Ingestion Complete"
                  : phase === "uploading"
                  ? "Stage 1 Active"
                  : phase === "indexing"
                  ? "Stages 2-4 Active"
                  : "Phases 07-10 Ready"}
              </span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-[12px] w-full">
              {/* Stage 1 */}
              <div
                className={`p-3.5 rounded-lg border flex flex-col gap-1 min-w-0 transition-colors ${
                  phase === "uploading"
                    ? "bg-[#EEF3FF] border-[#4D80FC] text-[#1B1A1A]"
                    : phase === "indexing" || phase === "completed"
                    ? "bg-[#EDF9ED] border-[#C5ECC4] text-[#1B1A1A]"
                    : "bg-[#F5F6F9] border-[#EDEFF3] text-[#1B1A1A]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-[#4D80FC] font-semibold">
                    STAGE 1
                  </span>
                  {phase === "indexing" || phase === "completed" ? (
                    <Icon name="check_circle" size={14} className="text-[#65C861]" />
                  ) : phase === "uploading" ? (
                    <div className="w-3 h-3 border border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
                  ) : null}
                </div>
                <span className="font-medium text-[#1B1A1A]">Upload Job</span>
                <span className="text-[11px] text-[#606366] leading-tight">
                  {phase === "uploading"
                    ? "Streaming file to disk..."
                    : phase === "indexing" || phase === "completed"
                    ? "Stored in PostgreSQL"
                    : "PostgreSQL metadata store"}
                </span>
              </div>

              {/* Stage 2 */}
              <div
                className={`p-3.5 rounded-lg border flex flex-col gap-1 min-w-0 transition-colors ${
                  phase === "indexing"
                    ? "bg-[#EEF3FF] border-[#4D80FC] text-[#1B1A1A]"
                    : phase === "completed"
                    ? "bg-[#EDF9ED] border-[#C5ECC4] text-[#1B1A1A]"
                    : "bg-[#F5F6F9] border-[#EDEFF3] text-[#1B1A1A]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-[#4D80FC] font-semibold">
                    STAGE 2
                  </span>
                  {phase === "completed" ? (
                    <Icon name="check_circle" size={14} className="text-[#65C861]" />
                  ) : phase === "indexing" ? (
                    <div className="w-3 h-3 border border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
                  ) : null}
                </div>
                <span className="font-medium text-[#1B1A1A]">Format Parsing</span>
                <span className="text-[11px] text-[#606366] leading-tight">
                  {phase === "indexing"
                    ? "Parsing structure..."
                    : phase === "completed"
                    ? "Parsing verified"
                    : "Multi-format ingest parser"}
                </span>
              </div>

              {/* Stage 3 */}
              <div
                className={`p-3.5 rounded-lg border flex flex-col gap-1 min-w-0 transition-colors ${
                  phase === "indexing"
                    ? "bg-[#EEF3FF] border-[#4D80FC] text-[#1B1A1A]"
                    : phase === "completed"
                    ? "bg-[#EDF9ED] border-[#C5ECC4] text-[#1B1A1A]"
                    : "bg-[#F5F6F9] border-[#EDEFF3] text-[#1B1A1A]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-[#4D80FC] font-semibold">
                    STAGE 3
                  </span>
                  {phase === "completed" ? (
                    <Icon name="check_circle" size={14} className="text-[#65C861]" />
                  ) : phase === "indexing" ? (
                    <div className="w-3 h-3 border border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
                  ) : null}
                </div>
                <span className="font-medium text-[#1B1A1A]">Normalization</span>
                <span className="text-[11px] text-[#606366] leading-tight">
                  {phase === "indexing"
                    ? "Applying canonical schema..."
                    : phase === "completed"
                    ? "Schema normalized"
                    : "Canonical schema mapping"}
                </span>
              </div>

              {/* Stage 4 */}
              <div
                className={`p-3.5 rounded-lg border flex flex-col gap-1 min-w-0 transition-colors ${
                  phase === "indexing"
                    ? "bg-[#EEF3FF] border-[#4D80FC] text-[#1B1A1A]"
                    : phase === "completed"
                    ? "bg-[#EDF9ED] border-[#C5ECC4] text-[#1B1A1A]"
                    : "bg-[#F5F6F9] border-[#EDEFF3] text-[#1B1A1A]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[10px] text-[#4D80FC] font-semibold">
                    STAGE 4
                  </span>
                  {phase === "completed" ? (
                    <Icon name="check_circle" size={14} className="text-[#65C861]" />
                  ) : phase === "indexing" ? (
                    <div className="w-3 h-3 border border-[#4D80FC] border-t-transparent rounded-full animate-spin" />
                  ) : null}
                </div>
                <span className="font-medium text-[#1B1A1A]">OpenSearch Index</span>
                <span className="text-[11px] text-[#606366] leading-tight">
                  {phase === "indexing"
                    ? "Executing bulk index..."
                    : phase === "completed"
                    ? `${successResult?.indexing.indexed_records ?? 0} docs indexed`
                    : "Bulk cluster indexing"}
                </span>
              </div>
            </div>
          </div>
        </div>

        {/* Secondary Right Column: API Target Info & Formats */}
        <div className="flex flex-col gap-5 min-w-0 w-full">
          {/* Endpoint Card */}
          <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] shadow-xs flex flex-col gap-3 w-full">
            <h2 className="text-[14px] font-semibold text-[#1B1A1A]">
              Upload Endpoint Configuration
            </h2>
            <div className="flex flex-col gap-2.5 text-[12px]">
              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
                  Target API Route
                </span>
                <div className="p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] font-mono text-[11px] text-[#1B1A1A] break-all">
                  {API_ENDPOINTS.logs.upload}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="px-2 py-0.5 rounded font-mono text-[10px] bg-[#EEF3FF] text-[#4D80FC] border border-[#BFDBFE] font-semibold">
                  POST multipart/form-data
                </span>
                <span className="font-mono text-[11px] text-[#9B9FA3]">
                  file: UploadFile
                </span>
              </div>
              <div className="flex flex-col gap-1 pt-1">
                <span className="text-[11px] font-semibold text-[#9B9FA3] uppercase tracking-wider">
                  Indexing API Route
                </span>
                <div className="p-2.5 rounded-lg bg-[#F5F6F9] border border-[#EDEFF3] font-mono text-[11px] text-[#1B1A1A] break-all">
                  {API_ENDPOINTS.logs.base}/jobs/&#123;job_id&#125;/index
                </div>
              </div>
              <p className="text-[12px] text-[#606366] leading-relaxed pt-1">
                The interactive dropzone sends file payloads to this endpoint, registers an upload job, and triggers OpenSearch bulk indexing.
              </p>
            </div>
          </div>

          {/* Supported Format Matrix Card */}
          <div className="p-5 rounded-xl bg-white border border-[#EDEFF3] shadow-xs flex flex-col gap-3 w-full">
            <h2 className="text-[14px] font-semibold text-[#1B1A1A]">
              Allowed Log Extensions
            </h2>
            <div className="flex flex-wrap gap-1.5 text-[11px] font-mono">
              {ALLOWED_EXTENSIONS.map((ext) => (
                <span
                  key={ext}
                  className="px-2 py-1 rounded bg-[#F5F6F9] border border-[#EDEFF3] text-[#1B1A1A] font-semibold"
                >
                  {ext}
                </span>
              ))}
            </div>
            <p className="text-[12px] text-[#9B9FA3] leading-relaxed pt-1">
              File content headers are inspected during Stage 2 to determine parser strategy automatically.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
