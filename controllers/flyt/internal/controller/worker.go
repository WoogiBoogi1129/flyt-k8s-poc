package controller

import (
    "context"
    "fmt"
    "reflect"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)
type WorkerReconciler struct{Base}
func (r *WorkerReconciler) block(ctx context.Context,w *api.FlytWorker,cp *api.FlytControlPlane,reason,msg string)(ctrl.Result,error){
    if err:=r.revoke(ctx,w,cp);err!=nil{return r.mark(ctx,w,"Blocked","UnregisterPending",err.Error())}
    return r.mark(ctx,w,"Blocked",reason,msg)
}
func clearRuntime(s *api.Status){s.Endpoint="";s.PodUID="";s.PodIP="";s.VMIP="";s.WorkerGeneration="";s.ManagerEpoch=""}
func (r *WorkerReconciler) stop(ctx context.Context,w *api.FlytWorker,cp *api.FlytControlPlane,phase,reason,msg string)(ctrl.Result,error){
    if err:=r.revoke(ctx,w,cp);err!=nil{return r.mark(ctx,w,"Terminating","UnregisterPending",err.Error())}
    done,err:=r.stopResources(ctx,w,"worker");if err!=nil{return ctrl.Result{},err}
    if !done{return r.mark(ctx,w,"Terminating","ReleasePending","Waiting for Worker Pod removal; network policy retained and GPU release is not inferred")}
    if !w.DeletionTimestamp.IsZero(){_,err=r.finalizer(ctx,w,false);return ctrl.Result{},err}
    err=r.status(ctx,w,phase,reason,msg,clearRuntime);return ctrl.Result{RequeueAfter:Period},err
}
func (b *Base) podBelongs(ctx context.Context,p *corev1.Pod,w *api.FlytWorker)error{
    podOwner:=metav1.GetControllerOf(p);if podOwner==nil||podOwner.Kind!="ReplicaSet"{return fmt.Errorf("Worker Pod has no expected ReplicaSet owner")}
    rs:=&appsv1.ReplicaSet{};if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:w.Namespace,Name:podOwner.Name},rs);err!=nil{return err}
    depOwner:=metav1.GetControllerOf(rs);if rs.UID!=podOwner.UID||depOwner==nil||depOwner.Kind!="Deployment"||depOwner.Name!=w.Name{return fmt.Errorf("Worker ReplicaSet identity differs")}
    dep:=&appsv1.Deployment{};if err:=b.Reader.Get(ctx,types.NamespacedName{Namespace:w.Namespace,Name:w.Name},dep);err!=nil{return err}
    if depOwner.UID!=dep.UID||!ownedBy(dep,w){return fmt.Errorf("Worker Deployment identity differs")};return nil
}
func (r *WorkerReconciler) Reconcile(ctx context.Context,req ctrl.Request)(ctrl.Result,error){
    w:=&api.FlytWorker{};if err:=r.Reader.Get(ctx,req.NamespacedName,w);err!=nil{return ctrl.Result{},client.IgnoreNotFound(err)}
    if w.Name!=workerName(w.Spec.VMIRef.UID){return r.mark(ctx,w,"Blocked","InvalidName","Worker name must be fw- followed by VMI UID with hyphens removed")}
    cp:=&api.FlytControlPlane{};err:=r.Reader.Get(ctx,types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.ControlPlaneRef.Name},cp)
    if err!=nil&&!apierrors.IsNotFound(err){return ctrl.Result{},err}
    if apierrors.IsNotFound(err)||string(cp.UID)!=w.Spec.ControlPlaneRef.UID{cp=nil}
    if !w.DeletionTimestamp.IsZero(){return r.stop(ctx,w,cp,"Terminating","Deleting","Worker deletion requested")}
    if changed,err:=r.finalizer(ctx,w,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    v:=vmi();err=r.Reader.Get(ctx,types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.VMIRef.Name},v)
    if err!=nil&&!apierrors.IsNotFound(err){return ctrl.Result{},err}
    if apierrors.IsNotFound(err)||string(v.GetUID())!=w.Spec.VMIRef.UID||terminal(v){return ctrl.Result{RequeueAfter:Period},r.deleteWorker(ctx,w)}
    if changed,err:=r.finalizer(ctx,v,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    // Manual CRs receive the same VMI ownership link as VMI-created CRs.
    if len(w.OwnerReferences)==0{
        old:=w.DeepCopy();w.OwnerReferences=[]metav1.OwnerReference{{APIVersion:"kubevirt.io/v1",Kind:"VirtualMachineInstance",Name:v.GetName(),UID:v.GetUID(),Controller:boolp(false),BlockOwnerDeletion:boolp(false)}}
        return ctrl.Result{Requeue:true},r.Patch(ctx,w,client.MergeFromWithOptions(old,client.MergeFromWithOptimisticLock{}))
    }
    if len(w.OwnerReferences)!=1||w.OwnerReferences[0].UID!=v.GetUID(){return r.block(ctx,w,cp,"OwnershipConflict","FlytWorker has a different owner")}
    if w.Spec.Suspend{return r.stop(ctx,w,cp,"Suspended","Suspended","Worker resources released; resume requires fresh CUDA clients")}
    if cp==nil{return r.stop(ctx,w,nil,"Blocked","ControlPlaneMissing","ControlPlane UID no longer exists")}
    if !cp.DeletionTimestamp.IsZero()&&w.Status.VMIP==""{return r.mark(ctx,w,"Blocked","ControlPlaneDeleting","New allocations are blocked while the control plane is deleting")}
    p:=&api.FlytGPUProfile{};err=r.Reader.Get(ctx,types.NamespacedName{Namespace:w.Namespace,Name:w.Spec.ProfileRef.Name},p)
    if err!=nil&&!apierrors.IsNotFound(err){return ctrl.Result{},err}
    if apierrors.IsNotFound(err)||string(p.UID)!=w.Spec.ProfileRef.UID{return r.stop(ctx,w,cp,"Blocked","ProfileMissing","GPU profile UID no longer exists")}
    if !p.DeletionTimestamp.IsZero()&&w.Status.VMIP==""{return r.mark(ctx,w,"Blocked","ProfileDeleting","New allocations are blocked while the profile is deleting")}
    if err=r.profileReady(ctx,p);err!=nil{return r.stop(ctx,w,cp,"Blocked","ProfileUnavailable",err.Error())}
    if err=r.workerRequest(ctx,w,v,p);err!=nil{return r.stop(ctx,w,cp,"Blocked","RequestUnavailable",err.Error())}
    ip:=vmIP(v)
    if phase(v)!="Running"||ip==""{return r.stop(ctx,w,cp,"Pending","WaitingForVMI","Waiting for a Running VMI with a pod-network IPv4 address")}
    if w.Status.VMIP!=""&&w.Status.VMIP!=ip{return r.stop(ctx,w,cp,"Pending","VMIPChanged","Old network identity removed; recreate Worker before admitting new sessions")}
    // Persist network identity BEFORE creating resources, making cleanup/recovery
    // safe even if the operator crashes between individual API writes.
    if w.Status.VMIP==""{
        err=r.status(ctx,w,"Pending","Preparing","Preparing resources for this VMI identity",func(s *api.Status){s.VMIP=ip})
        return ctrl.Result{Requeue:true},err
    }
    for _,o:=range workerResources(w,p,cp,ip){if err=r.ensure(ctx,w,o);err!=nil{return r.block(ctx,w,cp,"ApplyFailed",err.Error())}}
    pods,err:=r.pods(ctx,w,"worker");if err!=nil{return ctrl.Result{},err}
    live:=[]corev1.Pod{};for _,pod:=range pods{if active(pod){live=append(live,pod)}}
    if len(live)!=1||!podReady(live[0]){
        if err=r.revoke(ctx,w,cp);err!=nil{return r.mark(ctx,w,"Pending","UnregisterPending",err.Error())}
        return r.mark(ctx,w,"Pending","WaitingForWorker","Exactly one ready Worker Pod is required; CUDA correctness remains unvalidated")
    }
    pod:=&live[0];if err=r.podBelongs(ctx,pod,w);err!=nil{return r.block(ctx,w,cp,"OwnershipConflict",err.Error())}
    if pod.Status.PodIP==""||pod.Spec.NodeName!=p.Spec.NodeName||pod.Annotations["nvidia.com/use-gpuuuid"]!=p.Spec.GPUUUID{return r.block(ctx,w,cp,"AllocationMismatch","Worker node, Pod IP or GPU UUID differs from approved allocation")}
    seen,err:=r.binding(ctx,cp,map[string]interface{}{"op":"observe","pod_uid":string(pod.UID),"pod_ip":pod.Status.PodIP})
    if err!=nil{return r.mark(ctx,w,"Pending","RegistrationPending",err.Error())}
    desired:=Binding{WorkerUID:string(w.UID),VMIUID:w.Spec.VMIRef.UID,VMIP:ip,PodUID:string(pod.UID),PodIP:pod.Status.PodIP,Generation:seen.Generation}
    current,err:=r.binding(ctx,cp,map[string]interface{}{"op":"list"});if err!=nil{return ctrl.Result{},err}
    for _,existing:=range current.Bindings{
        if existing.WorkerUID==string(w.UID)&&!reflect.DeepEqual(existing,desired){
            if err=r.revoke(ctx,w,cp);err!=nil{return ctrl.Result{},err}
            return r.mark(ctx,w,"Pending","PreviousGenerationRevoked","Previous binding removed; waiting for current Worker registration")
        }
    }
    bound,err:=r.binding(ctx,cp,map[string]interface{}{"op":"bind","binding":desired});if err!=nil{return r.mark(ctx,w,"Pending","BindingPending",err.Error())}
    err=r.status(ctx,w,"Ready","Bound","Current VMI and Worker generation registered; CUDA sessions are not restored across restarts",func(s *api.Status){
        s.PodUID=string(pod.UID);s.PodIP=pod.Status.PodIP;s.WorkerGeneration=seen.Generation;s.ManagerEpoch=bound.Epoch
        s.Endpoint=pod.Status.PodIP+":111";s.VMIP=ip})
    return ctrl.Result{RequeueAfter:Period},err
}
