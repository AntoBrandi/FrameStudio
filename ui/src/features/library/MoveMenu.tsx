import { Folder, FolderInput, FolderX, Library, SquareKanban } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, type ButtonProps } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/misc";
import { type MoveTarget, useFolders, useProjects } from "./api";
import { folderColor } from "./colors";

function Option({ icon, label, onSelect }: { icon: ReactNode; label: string; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onSelect}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-raised [&_svg]:size-4 [&_svg]:shrink-0"
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <p className="px-2 pb-1 pt-2 font-mono text-[10px] uppercase tracking-widest text-muted">{children}</p>;
}

/** "Move to…": this project's folders, the library and its folders, other projects. */
export function MoveMenu({
  projectId,
  onMove,
  triggerProps,
}: {
  projectId?: string;
  onMove: (target: MoveTarget) => void;
  triggerProps?: ButtonProps;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const { data: projectFolders = [] } = useFolders(projectId);
  const { data: globalFolders = [] } = useFolders(undefined);
  const { data: projects = [] } = useProjects({ sort: "updated" });
  const pick = (target: MoveTarget) => {
    setOpen(false);
    onMove(target);
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" {...triggerProps}>
          <FolderInput />
          {t("library.moveTo")}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="max-h-96 w-72 overflow-auto p-2"
        align="start"
        role="menu"
        aria-label={t("library.moveTo")}
      >
        {projectId ? (
          <>
            <Heading>{t("library.thisProject")}</Heading>
            <Option
              icon={<FolderX />}
              label={t("library.projectRoot")}
              onSelect={() => pick({ project_id: projectId })}
            />
            {projectFolders.map((folder) => (
              <Option
                key={folder.id}
                icon={<Folder style={{ color: folderColor(folder.color) }} />}
                label={folder.name}
                onSelect={() => pick({ folder_id: folder.id })}
              />
            ))}
          </>
        ) : null}
        <Heading>{t("library.allAssets")}</Heading>
        <Option icon={<Library />} label={t("library.libraryRoot")} onSelect={() => pick({ project_id: null })} />
        {globalFolders.map((folder) => (
          <Option
            key={folder.id}
            icon={<Folder style={{ color: folderColor(folder.color) }} />}
            label={folder.name}
            onSelect={() => pick({ folder_id: folder.id })}
          />
        ))}
        {projects.filter((p) => p.id !== projectId).length ? <Heading>{t("library.projects")}</Heading> : null}
        {projects
          .filter((p) => p.id !== projectId)
          .map((project) => (
            <Option
              key={project.id}
              icon={<SquareKanban />}
              label={project.name}
              onSelect={() => pick({ project_id: project.id })}
            />
          ))}
      </PopoverContent>
    </Popover>
  );
}
