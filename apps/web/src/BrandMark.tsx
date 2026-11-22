export default function BrandMark({
  size = 24,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M5 11V7a2 2 0 0 1 2-2h4M21 5h4a2 2 0 0 1 2 2v4M27 21v4a2 2 0 0 1-2 2h-4M11 27H7a2 2 0 0 1-2-2v-4"
        stroke="currentColor"
        strokeWidth="2.3"
        strokeLinecap="round"
      />
      <path
        d="M7.5 16s3-5.5 8.5-5.5 8.5 5.5 8.5 5.5-3 5.5-8.5 5.5S7.5 16 7.5 16Z"
        stroke="currentColor"
        strokeWidth="1.8"
      />
      <circle cx="16" cy="16" r="3" fill="currentColor" />
    </svg>
  );
}
